import { startTransition, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AppHeader } from "../components/AppHeader";
import { MessageBubble, type UiMessage } from "../components/MessageBubble";
import { SessionRail, type SessionPreview } from "../components/SessionRail";
import {
  type AccountSummary,
  ApiError,
  type SessionSummary,
  createSession,
  getChatBootstrap,
  getMe,
  getSessionHistory,
  logout,
  sendMessage,
  streamMessage,
  type UiMe,
} from "../lib/api";
import { consumeEventStream } from "../lib/streaming";

type StatusTone = "info" | "error" | "success";

type ChatStatus = {
  tone: StatusTone;
  message: string;
} | null;

function trimPreview(text: string, maxLength = 96) {
  const singleLine = text.replace(/\s+/g, " ").trim();
  if (singleLine.length <= maxLength) {
    return singleLine;
  }
  return `${singleLine.slice(0, maxLength - 3)}...`;
}

function buildSessionPreview(messages: UiMessage[], fallback: string): SessionPreview {
  const firstUserMessage = messages.find((message) => message.role === "user" && message.content.trim());
  const latestMessage = [...messages]
    .reverse()
    .find((message) => (message.role === "assistant" || message.role === "user") && message.content.trim());

  return {
    title: firstUserMessage ? trimPreview(firstUserMessage.content, 40) : fallback,
    preview: latestMessage ? trimPreview(latestMessage.content) : "Conversation context is ready for the next turn.",
  };
}

function mapHistoryToMessages(items: Array<{ role: "user" | "assistant" | "system"; content: string; created_at: string | null }>) {
  return items.map<UiMessage>((item, index) => ({
    id: `${item.role}-${item.created_at ?? index}`,
    role: item.role,
    content: item.content,
    createdAt: item.created_at,
  }));
}

export function ChatPage() {
  const navigate = useNavigate();
  const [me, setMe] = useState<UiMe | null>(null);
  const [accounts, setAccounts] = useState<AccountSummary[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionMessages, setSessionMessages] = useState<Record<string, UiMessage[]>>({});
  const [sessionPreviews, setSessionPreviews] = useState<Record<string, SessionPreview>>({});
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [loadingBootstrap, setLoadingBootstrap] = useState(true);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [composerValue, setComposerValue] = useState("");
  const [streamEnabled, setStreamEnabled] = useState(true);
  const [temporaryMode, setTemporaryMode] = useState(false);
  const [allowFailover, setAllowFailover] = useState(false);
  const [pinnedAccountId, setPinnedAccountId] = useState("");
  const [chatStatus, setChatStatus] = useState<ChatStatus>(null);
  const [isSending, setIsSending] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isAtBottom, setIsAtBottom] = useState(true);

  const messageViewportRef = useRef<HTMLDivElement | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const streamBufferRef = useRef("");
  const streamMessageIdRef = useRef<string | null>(null);
  const streamSessionIdRef = useRef<string | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    void Promise.all([getMe(), getChatBootstrap()])
      .then(async ([currentMe, bootstrap]) => {
        if (cancelled) {
          return;
        }
        if (!currentMe.authenticated) {
          navigate("/ui/login", { replace: true });
          return;
        }
        setMe(currentMe);
        setAccounts(bootstrap.accounts);
        setSessions(bootstrap.sessions);
        if (bootstrap.sessions[0]) {
          setSelectedSessionId(bootstrap.sessions[0].session_id);
          await loadHistory(bootstrap.sessions[0].session_id, bootstrap.sessions[0], cancelled);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          if (error instanceof ApiError && error.status === 401) {
            navigate("/ui/login", { replace: true });
            return;
          }
          setChatStatus({
            tone: "error",
            message: error instanceof Error ? error.message : "Failed to load the chat workspace.",
          });
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingBootstrap(false);
        }
      });

    return () => {
      cancelled = true;
      abortControllerRef.current?.abort();
      if (rafRef.current !== null) {
        window.cancelAnimationFrame(rafRef.current);
      }
    };
  }, [navigate]);

  useEffect(() => {
    if (!isAtBottom || !messageViewportRef.current) {
      return;
    }
    messageViewportRef.current.scrollTo({
      top: messageViewportRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [isAtBottom, selectedSessionId, sessionMessages]);

  async function handleLogout() {
    await logout();
    navigate("/ui/login", { replace: true });
  }

  async function loadHistory(sessionId: string, session?: SessionSummary, cancelled = false) {
    setLoadingHistory(true);
    setChatStatus(null);
    try {
      const history = await getSessionHistory(sessionId);
      if (cancelled) {
        return;
      }
      const mappedMessages = mapHistoryToMessages(history.items);
      setSessionMessages((current) => ({
        ...current,
        [sessionId]: mappedMessages,
      }));
      setSessionPreviews((current) => ({
        ...current,
        [sessionId]: buildSessionPreview(
          mappedMessages,
          session ? session.session_id.slice(0, 8) : sessionId.slice(0, 8),
        ),
      }));
    } catch (error) {
      if (!cancelled) {
        setChatStatus({
          tone: "error",
          message: error instanceof Error ? error.message : "Failed to load the selected conversation.",
        });
      }
    } finally {
      if (!cancelled) {
        setLoadingHistory(false);
      }
    }
  }

  function patchSessionList(nextSession: SessionSummary, previewText?: string) {
    setSessions((current) => {
      const existing = current.filter((session) => session.session_id !== nextSession.session_id);
      return [nextSession, ...existing];
    });
    if (previewText) {
      setSessionPreviews((current) => ({
        ...current,
        [nextSession.session_id]: {
          ...(current[nextSession.session_id] || {}),
          preview: trimPreview(previewText),
          title: current[nextSession.session_id]?.title || trimPreview(previewText, 40),
        },
      }));
    }
  }

  async function handleCreateSession(selectAfterCreate = true) {
    setChatStatus(null);
    const session = await createSession({
      routing_policy: "sticky",
      account_id: me?.is_admin ? pinnedAccountId || null : null,
      allow_failover: me?.is_admin ? allowFailover : false,
    });
    patchSessionList(session);
    setSessionMessages((current) => ({
      ...current,
      [session.session_id]: [],
    }));
    if (selectAfterCreate) {
      setSelectedSessionId(session.session_id);
    }
    return session;
  }

  async function handleSelectSession(sessionId: string) {
    setSelectedSessionId(sessionId);
    const session = sessions.find((item) => item.session_id === sessionId);
    if (!sessionMessages[sessionId]) {
      await loadHistory(sessionId, session);
    }
  }

  function appendMessage(sessionId: string, message: UiMessage) {
    setSessionMessages((current) => {
      const existing = current[sessionId] || [];
      return {
        ...current,
        [sessionId]: [...existing, message],
      };
    });
  }

  function updateMessage(sessionId: string, messageId: string, updater: (message: UiMessage) => UiMessage) {
    setSessionMessages((current) => ({
      ...current,
      [sessionId]: (current[sessionId] || []).map((message) =>
        message.id === messageId ? updater(message) : message,
      ),
    }));
  }

  function flushBufferedChunks() {
    if (!streamSessionIdRef.current || !streamMessageIdRef.current) {
      return;
    }

    const textDelta = streamBufferRef.current;
    streamBufferRef.current = "";
    rafRef.current = null;

    if (!textDelta) {
      return;
    }

    updateMessage(streamSessionIdRef.current, streamMessageIdRef.current, (message) => ({
      ...message,
      content: message.content + textDelta,
      isThinking: false,
      isStreaming: true,
    }));

    if (streamBufferRef.current) {
      rafRef.current = window.requestAnimationFrame(flushBufferedChunks);
    }
  }

  function queueChunkFlush(textDelta: string) {
    streamBufferRef.current += textDelta;
    if (rafRef.current === null) {
      rafRef.current = window.requestAnimationFrame(flushBufferedChunks);
    }
  }

  async function handleSendMessage() {
    const trimmed = composerValue.trim();
    if (!trimmed || isSending || !me) {
      return;
    }

    setIsSending(true);
    setChatStatus(null);
    let targetSession = sessions.find((session) => session.session_id === selectedSessionId) || null;

    try {
      if (!targetSession) {
        targetSession = await handleCreateSession(true);
      }

      if (!targetSession) {
        throw new Error("Failed to create a new session.");
      }

      const sessionId = targetSession.session_id;
      setSelectedSessionId(sessionId);
      setComposerValue("");

      const userMessage: UiMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: trimmed,
        createdAt: new Date().toISOString(),
      };
      const assistantMessageId = crypto.randomUUID();
      const assistantPlaceholder: UiMessage = {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        createdAt: new Date().toISOString(),
        isStreaming: streamEnabled,
        isThinking: streamEnabled,
      };

      appendMessage(sessionId, userMessage);
      appendMessage(sessionId, assistantPlaceholder);
      patchSessionList(
        {
          ...targetSession,
          updated_at: new Date().toISOString(),
        },
        trimmed,
      );

      if (!streamEnabled) {
        const response = await sendMessage({
          session_id: sessionId,
          message: trimmed,
          stream: false,
          temporary: temporaryMode,
          idempotency_key: crypto.randomUUID(),
        });
        startTransition(() => {
          updateMessage(sessionId, assistantMessageId, (message) => ({
            ...message,
            content: response.content,
            isStreaming: false,
            isThinking: false,
            createdAt: response.created_at,
          }));
        });
        setChatStatus({
          tone: "success",
          message: "Reply received.",
        });
        return;
      }

      const controller = new AbortController();
      abortControllerRef.current = controller;
      streamMessageIdRef.current = assistantMessageId;
      streamSessionIdRef.current = sessionId;
      streamBufferRef.current = "";
      setIsStreaming(true);

      const response = await streamMessage(
        {
          session_id: sessionId,
          message: trimmed,
          stream: true,
          temporary: temporaryMode,
          idempotency_key: crypto.randomUUID(),
        },
        controller.signal,
      );

      await consumeEventStream(response, async (event) => {
        if (event.type === "accepted") {
          setChatStatus({
            tone: "info",
            message: `Request accepted on ${event.payload.account_id}. Waiting for first token...`,
          });
          patchSessionList(
            {
              ...targetSession!,
              account_id: event.payload.account_id,
              updated_at: new Date().toISOString(),
            },
            trimmed,
          );
          return;
        }

        if (event.type === "status") {
          updateMessage(sessionId, assistantMessageId, (message) => ({
            ...message,
            isThinking: event.payload.phase !== "streaming",
            thinkingLabel: event.payload.message,
          }));
          if (event.payload.phase === "failover") {
            setChatStatus({
              tone: "info",
              message: event.payload.message,
            });
          }
          return;
        }

        if (event.type === "chunk") {
          queueChunkFlush(event.payload.text_delta || "");
          return;
        }

        if (event.type === "error") {
          if (rafRef.current !== null) {
            window.cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
          }
          streamBufferRef.current = "";
          updateMessage(sessionId, assistantMessageId, (message) => ({
            ...message,
            isStreaming: false,
            isThinking: false,
            errorText: event.payload.message,
          }));
          setChatStatus({
            tone: "error",
            message: event.payload.message,
          });
          return;
        }

        if (event.type === "done") {
          if (rafRef.current !== null) {
            window.cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
          }
          flushBufferedChunks();
          startTransition(() => {
            updateMessage(sessionId, assistantMessageId, (message) => ({
              ...message,
              content: event.payload.content,
              isStreaming: false,
              isThinking: false,
              createdAt: event.payload.created_at,
            }));
          });
          patchSessionList(
            {
              ...targetSession!,
              account_id: event.payload.account_id,
              updated_at: event.payload.created_at,
            },
            event.payload.content,
          );
          setChatStatus({
            tone: "success",
            message: "Streaming response completed.",
          });
        }
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setChatStatus({
          tone: "info",
          message: "Current response was cancelled.",
        });
      } else {
        setChatStatus({
          tone: "error",
          message: error instanceof Error ? error.message : "Failed to send the message.",
        });
      }
    } finally {
      abortControllerRef.current = null;
      streamMessageIdRef.current = null;
      streamSessionIdRef.current = null;
      streamBufferRef.current = "";
      if (rafRef.current !== null) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      setIsSending(false);
      setIsStreaming(false);
    }
  }

  function handleAbortStreaming() {
    abortControllerRef.current?.abort();
  }

  function handleViewportScroll() {
    if (!messageViewportRef.current) {
      return;
    }
    const { scrollTop, scrollHeight, clientHeight } = messageViewportRef.current;
    const nearBottom = scrollHeight - (scrollTop + clientHeight) < 80;
    setIsAtBottom(nearBottom);
  }

  const selectedMessages = selectedSessionId ? sessionMessages[selectedSessionId] || [] : [];
  const selectedSession = sessions.find((session) => session.session_id === selectedSessionId) || null;

  if (!me && loadingBootstrap) {
    return (
      <main className="page-shell">
        <section className="hero-card">
          <div className="hero-copy">
            <span className="eyebrow">Workspace Bootstrap</span>
            <h1>Loading the modern chat workspace...</h1>
            <p>The frontend is hydrating the routed session rail and chat context.</p>
          </div>
        </section>
      </main>
    );
  }

  if (!me) {
    return null;
  }

  return (
    <main className="page-shell chat-shell">
      <AppHeader
        me={me}
        badge="LAN Chat Workspace"
        title="Gemini conversation workspace"
        subtitle="A calmer, faster chat surface for routed Gemini sessions. Streaming stays local to the active bubble, while the rail and runtime controls update incrementally."
        actions={
          <button className="secondary-link compact-link button-reset" type="button" onClick={handleLogout}>
            Logout
          </button>
        }
      />

      <section className="chat-layout">
        <SessionRail
          sessions={sessions}
          selectedSessionId={selectedSessionId}
          previews={sessionPreviews}
          isLoading={loadingBootstrap}
          onCreateSession={() => {
            void handleCreateSession(true).catch((error) => {
              setChatStatus({
                tone: "error",
                message: error instanceof Error ? error.message : "Failed to create a session.",
              });
            });
          }}
          onSelectSession={(sessionId) => {
            void handleSelectSession(sessionId).catch((error) => {
              setChatStatus({
                tone: "error",
                message: error instanceof Error ? error.message : "Failed to load the session.",
              });
            });
          }}
        />

        <section className="chat-stage panel-surface">
          <div className="chat-stage__header">
            <div className="stack-sm">
              <span className="eyebrow">Active Session</span>
              <h2>{selectedSession ? selectedSession.session_id : "Ready when you are"}</h2>
              <p className="body-muted">
                {selectedSession
                  ? `Sticky account: ${selectedSession.account_id} - ${selectedSession.status}`
                  : "Start a new conversation or pick one from the rail."}
              </p>
            </div>
            <div className="chat-stage__toggles">
              {me.is_admin ? (
                <>
                  <label className="field compact-field">
                    <span>Account pinning</span>
                    <select value={pinnedAccountId} onChange={(event) => setPinnedAccountId(event.target.value)}>
                      <option value="">Auto-route to a healthy account</option>
                      {accounts.map((account) => (
                        <option key={account.account_id} value={account.account_id}>
                          {account.account_id} - {account.state}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="toggle-chip">
                    <input
                      checked={allowFailover}
                      onChange={(event) => setAllowFailover(event.target.checked)}
                      type="checkbox"
                    />
                    <span>Allow failover</span>
                  </label>
                </>
              ) : (
                <div className="routing-note">
                  Automatic routing is active. Your sessions are isolated to your own UI identity.
                </div>
              )}
            </div>
          </div>

          {chatStatus ? <div className={`inline-banner tone-${chatStatus.tone}`}>{chatStatus.message}</div> : null}

          <div className="chat-stage__body" data-testid="chat-messages" onScroll={handleViewportScroll} ref={messageViewportRef}>
            {loadingHistory ? <div className="chat-empty">Loading conversation history...</div> : null}
            {!loadingHistory && !selectedMessages.length ? (
              <div className="chat-empty">
                <div className="chat-empty__mark">*</div>
                <h3>Start the next useful thread.</h3>
                <p>
                  Streamed replies stay smooth, markdown is rendered only after completion, and the
                  rail updates without rebuilding the entire workspace.
                </p>
              </div>
            ) : null}
            {!loadingHistory
              ? selectedMessages.map((message) => <MessageBubble key={message.id} message={message} />)
              : null}
          </div>

          {!isAtBottom && selectedMessages.length ? (
            <button
              className="scroll-anchor"
              type="button"
              onClick={() => {
                messageViewportRef.current?.scrollTo({
                  top: messageViewportRef.current.scrollHeight,
                  behavior: "smooth",
                });
                setIsAtBottom(true);
              }}
            >
              Jump to latest
            </button>
          ) : null}

          <div className="composer-card">
            <label className="field">
              <span>Message</span>
              <textarea
                className="composer-input"
                data-testid="chat-composer"
                onChange={(event) => setComposerValue(event.target.value)}
                placeholder="Ask Gemini something useful. Streaming stays enabled by default."
                value={composerValue}
              />
            </label>
            <div className="composer-controls">
              <div className="composer-toggles">
                <label className="toggle-chip">
                  <input
                    checked={streamEnabled}
                    onChange={(event) => setStreamEnabled(event.target.checked)}
                    type="checkbox"
                  />
                  <span>Stream response</span>
                </label>
                <label className="toggle-chip">
                  <input
                    checked={temporaryMode}
                    onChange={(event) => setTemporaryMode(event.target.checked)}
                    type="checkbox"
                  />
                  <span>Temporary turn</span>
                </label>
              </div>
              <div className="composer-actions">
                {isStreaming ? (
                  <button className="secondary-link compact-link button-reset" type="button" onClick={handleAbortStreaming}>
                    Cancel stream
                  </button>
                ) : null}
                <button
                  className="primary-button"
                  data-testid="chat-send"
                  disabled={!composerValue.trim() || isSending}
                  type="button"
                  onClick={() => {
                    void handleSendMessage();
                  }}
                >
                  {isSending ? "Sending..." : "Send"}
                </button>
              </div>
            </div>
          </div>
        </section>
      </section>
    </main>
  );
}
