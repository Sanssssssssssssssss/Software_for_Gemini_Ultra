import type { SessionPreview } from "../../chat/types";
import type { SessionSummary } from "../../lib/api";

type ConversationHeaderProps = {
  preview?: SessionPreview;
  session: SessionSummary | null;
};

function translateSessionStatus(status: string) {
  switch (status) {
    case "active":
      return "ACTIVE";
    case "ready":
      return "READY";
    case "loading":
      return "LOADING";
    default:
      return status.toUpperCase();
  }
}

function formatSessionMeta(session: SessionSummary | null) {
  if (!session) {
    return "新会话会在发送第一条消息后自动创建。";
  }

  const route = session.account_id ? `路由 ${session.account_id}` : "等待分配账号";
  return `${route} · ${translateSessionStatus(session.status)}`;
}

export function ConversationHeader({ preview, session }: ConversationHeaderProps) {
  return (
    <header className="conversation-header">
      <div className="conversation-header__copy">
        <h2>{preview?.title || "新会话"}</h2>
        <p>{formatSessionMeta(session)}</p>
      </div>
    </header>
  );
}
