import { memo } from "react";

import type { UiMessage } from "../chat/types";
import { MessageAttachments } from "./MessageAttachments";
import { MarkdownMessage } from "./MarkdownMessage";
import { ThinkingIndicator } from "./ThinkingIndicator";

type MessageBubbleProps = {
  message: UiMessage;
};

function formatTimestamp(value?: string | null) {
  if (!value) {
    return "";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function MessageBubbleComponent({ message }: MessageBubbleProps) {
  const isAssistant = message.role === "assistant";
  const label = message.role === "user" ? "你" : message.role === "assistant" ? "Gemini" : "系统";

  return (
    <article className={`message-card role-${message.role}`}>
      <div className="message-card__meta">
        <strong>{label}</strong>
        {message.createdAt ? <span>{formatTimestamp(message.createdAt)}</span> : null}
      </div>
      {message.isThinking ? <ThinkingIndicator active={true} phaseLabel={message.thinkingLabel} /> : null}
      {message.errorText ? <div className="inline-error">{message.errorText}</div> : null}
      {message.isStreaming ? (
        <pre className="streaming-plain-text">{message.content || ""}</pre>
      ) : isAssistant ? (
        <MarkdownMessage content={message.content} />
      ) : (
        <pre className="streaming-plain-text">{message.content}</pre>
      )}
      <MessageAttachments media={message.media} parts={message.parts} />
    </article>
  );
}

export const MessageBubble = memo(MessageBubbleComponent);
export type { UiMessage };
