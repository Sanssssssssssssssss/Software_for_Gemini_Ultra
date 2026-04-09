import type { SessionHistoryItem, SessionSummary } from "../lib/api";
import type { SessionPreview, UiMessage } from "./types";

export const DEFAULT_SESSION_PREVIEW = "准备开始下一轮对话。";

export function trimPreview(text: string, maxLength = 96) {
  const singleLine = text.replace(/\s+/g, " ").trim();
  if (singleLine.length <= maxLength) {
    return singleLine;
  }
  return `${singleLine.slice(0, maxLength - 3)}...`;
}

export function getFallbackSessionTitle(sessionId: string, index = 0) {
  const sequence = String(Math.max(index, 0) + 1).padStart(2, "0");
  return `会话 ${sequence}`;
}

export function isGeneratedSessionTitle(title: string | undefined, sessionId: string, index = 0) {
  if (!title) {
    return true;
  }
  return title === getFallbackSessionTitle(sessionId, index) || /^会话 \d{2}$/.test(title) || /^Conversation \d{2}/.test(title);
}

export function buildSessionPreview(messages: UiMessage[], fallbackTitle = "新会话"): SessionPreview {
  const firstUserMessage = messages.find((message) => message.role === "user" && message.content.trim());
  const latestMessage = [...messages]
    .reverse()
    .find((message) => (message.role === "assistant" || message.role === "user") && message.content.trim());

  return {
    title: firstUserMessage ? trimPreview(firstUserMessage.content, 32) : fallbackTitle,
    preview: latestMessage ? trimPreview(latestMessage.content) : DEFAULT_SESSION_PREVIEW,
  };
}

export function getSessionTitle(session: SessionSummary, preview: SessionPreview | undefined, index: number) {
  return session.title || preview?.title || getFallbackSessionTitle(session.session_id, index);
}

export function mapHistoryToMessages(items: SessionHistoryItem[]) {
  return items.map<UiMessage>((item, index) => ({
    id: `${item.role}-${item.created_at ?? index}`,
    role: item.role,
    content: item.content,
    parts: item.parts,
    media: item.media,
    createdAt: item.created_at,
  }));
}
