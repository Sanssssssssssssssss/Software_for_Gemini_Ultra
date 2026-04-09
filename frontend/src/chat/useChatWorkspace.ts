import { startTransition, useEffect, useMemo, useRef, useState } from "react";
import type { NavigateFunction } from "react-router-dom";

import {
  type AccountSummary,
  type SessionSummary,
  ApiError,
  buildUiAssetContentUrl,
  createSession,
  deleteSession,
  getChatBootstrap,
  getMe,
  getSessionHistory,
  logout,
  sendMessage,
  streamMessage,
  updateSession,
  uploadAsset,
  type MessageResponsePart,
  type UiMe,
} from "../lib/api";
import { consumeEventStream } from "../lib/streaming";
import {
  DEFAULT_SESSION_PREVIEW,
  buildSessionPreview,
  getFallbackSessionTitle,
  isGeneratedSessionTitle,
  mapHistoryToMessages,
  trimPreview,
} from "./session-helpers";
import {
  DRAFT_SESSION_KEY,
  type ChatStatus,
  type PendingUpload,
  type SessionActivity,
  type SessionDraft,
  type SessionPreview,
  type UiMessage,
} from "./types";

type RuntimeState = {
  abortController: AbortController | null;
  streamQueue: string[];
  streamMessageId: string | null;
  hasFlushedFirstChunk: boolean;
  rafId: number | null;
};

type UseChatWorkspaceOptions = {
  navigate: NavigateFunction;
};

function createDefaultDraft(): SessionDraft {
  return {
    text: "",
    streamEnabled: true,
    temporaryMode: false,
    allowFailover: false,
    pinnedAccountId: "",
    pendingUploads: [],
  };
}

function createDefaultActivity(): SessionActivity {
  return {
    loadingHistory: false,
    isSending: false,
    isStreaming: false,
    isAtBottom: true,
    phaseLabel: null,
    status: null,
  };
}

function buildMessageParts(trimmed: string, readyUploads: PendingUpload[]) {
  return [
    ...(trimmed ? [{ type: "text", text: trimmed } satisfies MessageResponsePart] : []),
    ...readyUploads.map((upload) => ({ type: "asset", asset: upload.asset! } satisfies MessageResponsePart)),
  ];
}

function buildRequestParts(trimmed: string, readyUploads: PendingUpload[]) {
  return [
    ...(trimmed ? [{ type: "text" as const, text: trimmed }] : []),
    ...readyUploads.map((upload) => ({ type: "asset" as const, asset_id: upload.asset!.asset_id })),
  ];
}

function translateRuntimeMessage(message: string) {
  const normalized = message.trim().toLowerCase();
  if (normalized === "streaming reply") {
    return "正在生成回复";
  }
  if (normalized === "thinking") {
    return "思考中";
  }
  if (normalized === "reasoning") {
    return "推理中";
  }
  if (normalized === "drafting") {
    return "组织回答";
  }
  if (normalized === "polishing") {
    return "润色中";
  }
  if (normalized.includes("waiting for first token")) {
    return "正在等待首段回复...";
  }
  if (normalized.includes("failover")) {
    return "正在切换到可用账号...";
  }
  return message;
}

function segmentTextDelta(text: string, maxChars = 32) {
  const glyphs = Array.from(text);
  if (glyphs.length <= maxChars) {
    return [text];
  }
  const chunks: string[] = [];
  for (let index = 0; index < glyphs.length; index += maxChars) {
    chunks.push(glyphs.slice(index, index + maxChars).join(""));
  }
  return chunks;
}

export function useChatWorkspace({ navigate }: UseChatWorkspaceOptions) {
  const [me, setMe] = useState<UiMe | null>(null);
  const [accounts, setAccounts] = useState<AccountSummary[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionMessages, setSessionMessages] = useState<Record<string, UiMessage[]>>({});
  const [sessionPreviews, setSessionPreviews] = useState<Record<string, SessionPreview>>({});
  const [sessionDrafts, setSessionDrafts] = useState<Record<string, SessionDraft>>({
    [DRAFT_SESSION_KEY]: createDefaultDraft(),
  });
  const [sessionActivities, setSessionActivities] = useState<Record<string, SessionActivity>>({});
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [loadingBootstrap, setLoadingBootstrap] = useState(true);

  const messageViewportRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const runtimeRef = useRef<Record<string, RuntimeState>>({});
  const draftsRef = useRef(sessionDrafts);
  const activitiesRef = useRef(sessionActivities);
  const sessionsRef = useRef(sessions);

  useEffect(() => {
    draftsRef.current = sessionDrafts;
  }, [sessionDrafts]);

  useEffect(() => {
    activitiesRef.current = sessionActivities;
  }, [sessionActivities]);

  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);

  function ensureDraft(sessionId: string) {
    setSessionDrafts((current) => (current[sessionId] ? current : { ...current, [sessionId]: createDefaultDraft() }));
  }

  function ensureActivity(sessionId: string) {
    setSessionActivities((current) =>
      current[sessionId] ? current : { ...current, [sessionId]: createDefaultActivity() },
    );
  }

  function patchDraft(sessionId: string, updater: (draft: SessionDraft) => SessionDraft) {
    setSessionDrafts((current) => ({
      ...current,
      [sessionId]: updater(current[sessionId] ?? createDefaultDraft()),
    }));
  }

  function patchActivity(sessionId: string, updater: (activity: SessionActivity) => SessionActivity) {
    setSessionActivities((current) => ({
      ...current,
      [sessionId]: updater(current[sessionId] ?? createDefaultActivity()),
    }));
  }

  function setSessionStatus(sessionId: string, status: ChatStatus) {
    patchActivity(sessionId, (activity) => ({
      ...activity,
      status,
    }));
  }

  function getRuntime(sessionId: string) {
    if (!runtimeRef.current[sessionId]) {
      runtimeRef.current[sessionId] = {
        abortController: null,
        streamQueue: [],
        streamMessageId: null,
        hasFlushedFirstChunk: false,
        rafId: null,
      };
    }
    return runtimeRef.current[sessionId];
  }

  function cleanupRuntime(sessionId: string) {
    const runtime = runtimeRef.current[sessionId];
    if (!runtime) {
      return;
    }
    runtime.abortController = null;
    runtime.streamQueue = [];
    runtime.streamMessageId = null;
    runtime.hasFlushedFirstChunk = false;
    if (runtime.rafId !== null) {
      window.cancelAnimationFrame(runtime.rafId);
      runtime.rafId = null;
    }
  }

  function revokeUploadPreview(upload: PendingUpload) {
    if (upload.previewUrl) {
      URL.revokeObjectURL(upload.previewUrl);
    }
  }

  function clearPendingUploads(sessionId: string) {
    patchDraft(sessionId, (draft) => {
      draft.pendingUploads.forEach((upload) => {
        upload.abortController?.abort();
        revokeUploadPreview(upload);
      });
      return {
        ...draft,
        pendingUploads: [],
      };
    });
  }

  function moveDraft(sourceId: string, targetId: string) {
    setSessionDrafts((current) => {
      const source = current[sourceId] ?? createDefaultDraft();
      return {
        ...current,
        [sourceId]: createDefaultDraft(),
        [targetId]: source,
      };
    });
  }

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
        bootstrap.sessions.forEach((session) => {
          ensureDraft(session.session_id);
          ensureActivity(session.session_id);
        });
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
          setSessionActivities((current) => ({
            ...current,
            [DRAFT_SESSION_KEY]: {
              ...(current[DRAFT_SESSION_KEY] ?? createDefaultActivity()),
              status: {
                tone: "error",
                message: error instanceof Error ? error.message : "加载聊天工作区失败。",
              },
            },
          }));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingBootstrap(false);
        }
      });

    return () => {
      cancelled = true;
      Object.keys(runtimeRef.current).forEach((sessionId) => {
        runtimeRef.current[sessionId]?.abortController?.abort();
        cleanupRuntime(sessionId);
      });
      Object.values(draftsRef.current).forEach((draft) => {
        draft.pendingUploads.forEach(revokeUploadPreview);
      });
    };
  }, [navigate]);

  async function handleLogout() {
    await logout();
    navigate("/ui/login", { replace: true });
  }

  async function loadHistory(sessionId: string, session?: SessionSummary, cancelled = false) {
    patchActivity(sessionId, (activity) => ({
      ...activity,
      loadingHistory: true,
      status: null,
    }));

    try {
      const history = await getSessionHistory(sessionId);
      if (cancelled) {
        return;
      }

      const mappedMessages = mapHistoryToMessages(history.items);
      const fallbackTitle =
        session?.title ||
        getFallbackSessionTitle(
          session ? session.session_id : sessionId,
          sessionsRef.current.findIndex((item) => item.session_id === sessionId),
        );
      setSessionMessages((current) => ({
        ...current,
        [sessionId]: mappedMessages,
      }));
      setSessionPreviews((current) => ({
        ...current,
        [sessionId]: buildSessionPreview(mappedMessages, fallbackTitle),
      }));
    } catch (error) {
      if (!cancelled) {
        setSessionStatus(sessionId, {
          tone: "error",
          message: error instanceof Error ? error.message : "加载当前会话失败。",
        });
      }
    } finally {
      if (!cancelled) {
        patchActivity(sessionId, (activity) => ({
          ...activity,
          loadingHistory: false,
        }));
      }
    }
  }

  function patchSessionList(nextSession: SessionSummary, previewText?: string) {
    setSessions((current) => {
      const existing = current.filter((session) => session.session_id !== nextSession.session_id);
      return [nextSession, ...existing];
    });
    ensureDraft(nextSession.session_id);
    ensureActivity(nextSession.session_id);

    if (previewText) {
      setSessionPreviews((current) => ({
        ...current,
        [nextSession.session_id]: {
          ...(current[nextSession.session_id] || {}),
          preview: trimPreview(previewText),
          title:
            nextSession.title ||
            (isGeneratedSessionTitle(current[nextSession.session_id]?.title, nextSession.session_id)
              ? trimPreview(previewText, 32)
              : current[nextSession.session_id]?.title),
        },
      }));
    }
  }

  async function handleCreateSession(selectAfterCreate = true) {
    const sourceDraftKey = selectedSessionId ?? DRAFT_SESSION_KEY;
    const sourceDraft = draftsRef.current[sourceDraftKey] ?? createDefaultDraft();
    try {
      const session = await createSession({
        routing_policy: "sticky",
        account_id: me?.is_admin ? sourceDraft.pinnedAccountId || null : null,
        allow_failover: me?.is_admin ? sourceDraft.allowFailover : false,
      });

      patchSessionList(session);
      setSessionMessages((current) => ({
        ...current,
        [session.session_id]: [],
      }));
      setSessionPreviews((current) => ({
        ...current,
        [session.session_id]: {
          title: session.title || getFallbackSessionTitle(session.session_id, 0),
          preview: DEFAULT_SESSION_PREVIEW,
        },
      }));

      if (sourceDraftKey === DRAFT_SESSION_KEY && selectAfterCreate) {
        moveDraft(DRAFT_SESSION_KEY, session.session_id);
      }

      if (selectAfterCreate) {
        setSelectedSessionId(session.session_id);
      }

      return session;
    } catch (error) {
      setSessionStatus(sourceDraftKey, {
        tone: "error",
          message: error instanceof Error ? error.message : "创建会话失败。",
      });
      throw error;
    }
  }

  async function handleSelectSession(sessionId: string) {
    setSelectedSessionId(sessionId);
    if (!sessionMessages[sessionId]) {
      const session = sessionsRef.current.find((item) => item.session_id === sessionId);
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

  function flushBufferedChunks(sessionId: string) {
    const runtime = getRuntime(sessionId);
    if (!runtime.streamMessageId) {
      return;
    }

    const textDelta = runtime.streamQueue.shift() || "";
    runtime.rafId = null;

    if (!textDelta) {
      return;
    }

    updateMessage(sessionId, runtime.streamMessageId, (message) => ({
      ...message,
      content: message.content + textDelta,
      isThinking: false,
      isStreaming: true,
    }));

    if (runtime.streamQueue.length) {
      runtime.rafId = window.requestAnimationFrame(() => flushBufferedChunks(sessionId));
    }
  }

  function queueChunkFlush(sessionId: string, textDelta: string) {
    const runtime = getRuntime(sessionId);
    runtime.streamQueue.push(...segmentTextDelta(textDelta));
    if (!runtime.hasFlushedFirstChunk) {
      runtime.hasFlushedFirstChunk = true;
      flushBufferedChunks(sessionId);
      return;
    }
    if (runtime.rafId === null) {
      runtime.rafId = window.requestAnimationFrame(() => flushBufferedChunks(sessionId));
    }
  }

  function setComposerValue(value: string, sessionId = selectedSessionId ?? DRAFT_SESSION_KEY) {
    patchDraft(sessionId, (draft) => ({
      ...draft,
      text: value,
    }));
  }

  function updateDraftSetting<K extends keyof SessionDraft>(
    key: K,
    value: SessionDraft[K],
    sessionId = selectedSessionId ?? DRAFT_SESSION_KEY,
  ) {
    patchDraft(sessionId, (draft) => ({
      ...draft,
      [key]: value,
    }));
  }

  function updateUpload(sessionId: string, localId: string, updater: (upload: PendingUpload) => PendingUpload) {
    patchDraft(sessionId, (draft) => ({
      ...draft,
      pendingUploads: draft.pendingUploads.map((upload) =>
        upload.localId === localId ? updater(upload) : upload,
      ),
    }));
  }

  function enqueueFiles(fileList: FileList | File[], sessionId = selectedSessionId ?? DRAFT_SESSION_KEY) {
    const nextFiles = Array.from(fileList);
    if (!nextFiles.length) {
      return;
    }

    patchDraft(sessionId, (draft) => ({
      ...draft,
      temporaryMode: true,
    }));

    nextFiles.forEach((file) => {
      const localId = crypto.randomUUID();
      const previewUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined;
      const controller = new AbortController();
      const initialUpload: PendingUpload = {
        localId,
        file,
        status: "uploading",
        progress: 0,
        previewUrl,
        abortController: controller,
      };

      patchDraft(sessionId, (draft) => ({
        ...draft,
        pendingUploads: [...draft.pendingUploads, initialUpload],
      }));

      void uploadAsset(file, {
        temporary: true,
        signal: controller.signal,
        onProgress: (progress) => {
          updateUpload(sessionId, localId, (upload) => ({
            ...upload,
            progress,
          }));
        },
      })
        .then(({ asset }) => {
          updateUpload(sessionId, localId, (upload) => ({
            ...upload,
            status: "ready",
            progress: 100,
            asset,
            abortController: null,
          }));
          setSessionStatus(sessionId, {
            tone: "info",
            message: `${file.name} 已上传，发送消息时会一并提交。`,
          });
        })
        .catch((error) => {
          const isAbort = error instanceof DOMException && error.name === "AbortError";
          updateUpload(sessionId, localId, (upload) => ({
            ...upload,
            status: isAbort ? "cancelled" : "error",
            error: isAbort ? "上传已取消。" : error instanceof Error ? error.message : "上传失败。",
            abortController: null,
          }));
        });
    });
  }

  function retryUpload(localId: string, sessionId = selectedSessionId ?? DRAFT_SESSION_KEY) {
    const draft = draftsRef.current[sessionId] ?? createDefaultDraft();
    const target = draft.pendingUploads.find((item) => item.localId === localId);
    if (!target) {
      return;
    }
    revokeUploadPreview(target);
    patchDraft(sessionId, (current) => ({
      ...current,
      pendingUploads: current.pendingUploads.filter((item) => item.localId !== localId),
    }));
    enqueueFiles([target.file], sessionId);
  }

  function removeUpload(localId: string, sessionId = selectedSessionId ?? DRAFT_SESSION_KEY) {
    patchDraft(sessionId, (draft) => {
      const target = draft.pendingUploads.find((item) => item.localId === localId);
      target?.abortController?.abort();
      if (target) {
        revokeUploadPreview(target);
      }
      return {
        ...draft,
        pendingUploads: draft.pendingUploads.filter((item) => item.localId !== localId),
      };
    });
  }

  async function sendMessageForSelectedSession() {
    if (!me) {
      return;
    }

    const draftKey = selectedSessionId ?? DRAFT_SESSION_KEY;
    const draft = draftsRef.current[draftKey] ?? createDefaultDraft();
    const activity = activitiesRef.current[draftKey] ?? createDefaultActivity();
    const trimmed = draft.text.trim();
    const readyUploads = draft.pendingUploads.filter((item) => item.status === "ready" && item.asset);
    const hasUploading = draft.pendingUploads.some((item) => item.status === "uploading");

    if ((!trimmed && !readyUploads.length) || activity.isSending || hasUploading) {
      return;
    }

    patchActivity(draftKey, (current) => ({
      ...current,
      isSending: true,
      status: null,
    }));

    let targetSession = sessionsRef.current.find((session) => session.session_id === selectedSessionId) || null;
    let targetSessionId = selectedSessionId;

    try {
      if (!targetSession) {
        targetSession = await handleCreateSession(true);
        targetSessionId = targetSession.session_id;
      }

      if (!targetSessionId || !targetSession) {
        throw new Error("创建新会话失败。");
      }

      if (draftKey !== targetSessionId) {
        moveDraft(draftKey, targetSessionId);
      }

      const activeDraft = draftsRef.current[targetSessionId] ?? draft;
      const activeTrimmed = activeDraft.text.trim();
      const activeReadyUploads = activeDraft.pendingUploads.filter((item) => item.status === "ready" && item.asset);
      const messageParts = buildMessageParts(activeTrimmed, activeReadyUploads);

      setSelectedSessionId(targetSessionId);
      patchDraft(targetSessionId, (current) => ({
        ...current,
        text: "",
      }));

      const userMessage: UiMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: activeTrimmed || "[附件消息]",
        parts: messageParts,
        createdAt: new Date().toISOString(),
      };
      const assistantMessageId = crypto.randomUUID();
      const assistantPlaceholder: UiMessage = {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        createdAt: new Date().toISOString(),
        isStreaming: activeDraft.streamEnabled,
        isThinking: activeDraft.streamEnabled,
      };

      appendMessage(targetSessionId, userMessage);
      appendMessage(targetSessionId, assistantPlaceholder);
      patchSessionList(
        {
          ...targetSession,
          updated_at: new Date().toISOString(),
        },
        activeTrimmed,
      );

      if (!activeDraft.streamEnabled) {
        const response = await sendMessage({
          session_id: targetSessionId,
          ...(activeReadyUploads.length
            ? {
                parts: buildRequestParts(activeTrimmed, activeReadyUploads),
                message: null,
              }
            : { message: activeTrimmed }),
          stream: false,
          temporary: activeReadyUploads.length ? true : activeDraft.temporaryMode,
          idempotency_key: crypto.randomUUID(),
        });
        startTransition(() => {
          updateMessage(targetSessionId!, assistantMessageId, (message) => ({
            ...message,
            content: response.content,
            parts: response.parts,
            media: response.media,
            isStreaming: false,
            isThinking: false,
            createdAt: response.created_at,
          }));
        });
        clearPendingUploads(targetSessionId);
        setSessionStatus(targetSessionId, {
          tone: "success",
          message: "回复已返回。",
        });
        return;
      }

      const controller = new AbortController();
      const runtime = getRuntime(targetSessionId);
      runtime.abortController = controller;
      runtime.streamMessageId = assistantMessageId;
      runtime.streamQueue = [];
      runtime.hasFlushedFirstChunk = false;

      patchActivity(targetSessionId, (current) => ({
        ...current,
        isSending: true,
        isStreaming: true,
        phaseLabel: "正在等待首段回复...",
      }));

      const response = await streamMessage(
        {
          session_id: targetSessionId,
          ...(activeReadyUploads.length
            ? {
                parts: buildRequestParts(activeTrimmed, activeReadyUploads),
                message: null,
              }
            : { message: activeTrimmed }),
          stream: true,
          temporary: activeReadyUploads.length ? true : activeDraft.temporaryMode,
          idempotency_key: crypto.randomUUID(),
        },
        controller.signal,
      );
      clearPendingUploads(targetSessionId);

      await consumeEventStream(response, async (event) => {
        if (event.type === "accepted") {
          setSessionStatus(targetSessionId!, {
            tone: "info",
            message: `请求已由 ${event.payload.account_id} 接收，正在等待首段回复...`,
          });
          patchSessionList(
            {
              ...targetSession!,
              account_id: event.payload.account_id,
              updated_at: new Date().toISOString(),
            },
            activeTrimmed,
          );
          return;
        }

        if (event.type === "status") {
          const translatedMessage = translateRuntimeMessage(event.payload.message);
          updateMessage(targetSessionId!, assistantMessageId, (message) => ({
            ...message,
            isThinking: event.payload.phase !== "streaming",
            thinkingLabel: translatedMessage,
          }));
          patchActivity(targetSessionId!, (current) => ({
            ...current,
            phaseLabel: translatedMessage,
          }));
          if (event.payload.phase === "failover") {
            setSessionStatus(targetSessionId!, {
              tone: "info",
              message: translatedMessage,
            });
          }
          return;
        }

        if (event.type === "chunk") {
          queueChunkFlush(targetSessionId!, event.payload.text_delta || "");
          return;
        }

        if (event.type === "media") {
          updateMessage(targetSessionId!, assistantMessageId, (message) => ({
            ...message,
            media: [
              ...(message.media || []),
              {
                ...event.payload.asset,
                asset_id: event.payload.asset.asset_id,
                media_type: "image",
                content_url: buildUiAssetContentUrl(event.payload.asset.asset_id),
              },
            ],
          }));
          return;
        }

        if (event.type === "error") {
          cleanupRuntime(targetSessionId!);
          updateMessage(targetSessionId!, assistantMessageId, (message) => ({
            ...message,
            isStreaming: false,
            isThinking: false,
            errorText: translateRuntimeMessage(event.payload.message),
          }));
          patchActivity(targetSessionId!, (current) => ({
            ...current,
            isStreaming: false,
            phaseLabel: null,
          }));
          setSessionStatus(targetSessionId!, {
            tone: "error",
            message: translateRuntimeMessage(event.payload.message),
          });
          return;
        }

        if (event.type === "done") {
          cleanupRuntime(targetSessionId!);
          startTransition(() => {
            updateMessage(targetSessionId!, assistantMessageId, (message) => ({
              ...message,
              content: event.payload.content,
              parts: event.payload.parts,
              media: event.payload.media,
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
          patchActivity(targetSessionId!, (current) => ({
            ...current,
            isStreaming: false,
            phaseLabel: null,
          }));
          setSessionStatus(targetSessionId!, {
            tone: "success",
            message: "流式回复已完成。",
          });
        }
      });
    } catch (error) {
      const statusTarget = targetSessionId ?? draftKey;
      if (error instanceof DOMException && error.name === "AbortError") {
        setSessionStatus(statusTarget, {
          tone: "info",
          message: "已取消当前回复。",
        });
      } else {
        setSessionStatus(statusTarget, {
          tone: "error",
          message: error instanceof Error ? error.message : "发送消息失败。",
        });
      }
    } finally {
      if (targetSessionId) {
        cleanupRuntime(targetSessionId);
        patchActivity(targetSessionId, (current) => ({
          ...current,
          isSending: false,
          isStreaming: false,
          phaseLabel: null,
        }));
      }
      if (!targetSessionId || draftKey !== targetSessionId) {
        patchActivity(draftKey, (current) => ({
          ...current,
          isSending: false,
          isStreaming: false,
          phaseLabel: null,
        }));
      }
    }
  }

  function cancelStreaming(sessionId = selectedSessionId) {
    if (!sessionId) {
      return;
    }
    getRuntime(sessionId).abortController?.abort();
  }

  async function handleRenameSession(sessionId: string, title: string) {
    const updated = await updateSession(sessionId, { title });
    setSessions((current) =>
      current.map((session) => (session.session_id === sessionId ? { ...session, title: updated.title } : session)),
    );
    setSessionPreviews((current) => ({
      ...current,
      [sessionId]: {
        ...(current[sessionId] || {}),
        title: updated.title || current[sessionId]?.title,
      },
    }));
    return updated;
  }

  async function handleDeleteSession(sessionId: string) {
    const currentSessions = sessionsRef.current;
    const remaining = currentSessions.filter((session) => session.session_id !== sessionId);
    await deleteSession(sessionId);
    cleanupRuntime(sessionId);
    setSessions(remaining);
    setSessionMessages((current) => {
      const next = { ...current };
      delete next[sessionId];
      return next;
    });
    setSessionPreviews((current) => {
      const next = { ...current };
      delete next[sessionId];
      return next;
    });
    setSessionDrafts((current) => {
      const next = { ...current };
      delete next[sessionId];
      return next;
    });
    setSessionActivities((current) => {
      const next = { ...current };
      delete next[sessionId];
      return next;
    });

    if (selectedSessionId === sessionId) {
      const nextSelected = remaining[0]?.session_id ?? null;
      setSelectedSessionId(nextSelected);
      if (nextSelected && !sessionMessages[nextSelected]) {
        const nextSession = remaining.find((item) => item.session_id === nextSelected);
        await loadHistory(nextSelected, nextSession);
      }
    }
  }

  function handleViewportScroll() {
    const viewport = messageViewportRef.current;
    if (!viewport || !selectedSessionId) {
      return;
    }
    const { scrollTop, scrollHeight, clientHeight } = viewport;
    const nearBottom = scrollHeight - (scrollTop + clientHeight) < 80;
    patchActivity(selectedSessionId, (activity) => ({
      ...activity,
      isAtBottom: nearBottom,
    }));
  }

  function jumpToLatest() {
    if (!messageViewportRef.current || !selectedSessionId) {
      return;
    }
    messageViewportRef.current.scrollTo({
      top: messageViewportRef.current.scrollHeight,
      behavior: "smooth",
    });
    patchActivity(selectedSessionId, (activity) => ({
      ...activity,
      isAtBottom: true,
    }));
  }

  const selectedDraftKey = selectedSessionId ?? DRAFT_SESSION_KEY;
  const selectedDraft = sessionDrafts[selectedDraftKey] ?? createDefaultDraft();
  const selectedActivity = sessionActivities[selectedDraftKey] ?? createDefaultActivity();
  const selectedSession = useMemo(
    () => sessions.find((session) => session.session_id === selectedSessionId) || null,
    [selectedSessionId, sessions],
  );
  const selectedMessages = selectedSessionId ? sessionMessages[selectedSessionId] || [] : [];

  useEffect(() => {
    if (!selectedActivity.isAtBottom || !messageViewportRef.current) {
      return;
    }
    messageViewportRef.current.scrollTo({
      top: messageViewportRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [
    selectedActivity.isAtBottom,
    selectedMessages.length,
    selectedMessages[selectedMessages.length - 1]?.content,
    selectedSessionId,
  ]);

  return {
    accounts,
    fileInputRef,
    loadingBootstrap,
    me,
    messageViewportRef,
    selectedActivity,
    selectedDraft,
    selectedMessages,
    selectedSession,
    selectedSessionId,
    sessionActivities,
    sessionPreviews,
    sessions,
    handleCreateSession,
    handleDeleteSession,
    handleLogout,
    handleRenameSession,
    handleSelectSession,
    handleViewportScroll,
    jumpToLatest,
    loadHistory,
    sendMessageForSelectedSession,
    setComposerValue,
    setSelectedSessionId,
    updateDraftSetting,
    enqueueFiles,
    retryUpload,
    removeUpload,
    cancelStreaming,
  };
}
