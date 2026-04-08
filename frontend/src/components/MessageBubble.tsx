import type { MessageMedia, MessageResponsePart } from "../lib/api";
import { MessageAttachments } from "./MessageAttachments";
import { MarkdownMessage } from "./MarkdownMessage";
import { ThinkingIndicator } from "./ThinkingIndicator";

export type UiMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  createdAt?: string | null;
  isStreaming?: boolean;
  isThinking?: boolean;
  thinkingLabel?: string | null;
  errorText?: string | null;
  parts?: MessageResponsePart[];
  media?: MessageMedia[];
};

type MessageBubbleProps = {
  message: UiMessage;
};

export function MessageBubble({ message }: MessageBubbleProps) {
  const isAssistant = message.role === "assistant";
  const label = message.role === "user" ? "You" : message.role === "assistant" ? "Gemini" : "System";

  return (
    <article className={`message-card role-${message.role}`}>
      <div className="message-card__meta">
        <strong>{label}</strong>
        {message.createdAt ? <span>{message.createdAt}</span> : null}
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
