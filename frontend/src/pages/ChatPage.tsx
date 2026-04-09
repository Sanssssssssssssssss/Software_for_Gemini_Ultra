import { useNavigate } from "react-router-dom";

import { useChatWorkspace } from "../chat/useChatWorkspace";
import { ChatTopbar } from "../components/chat/ChatTopbar";
import { ConversationPane } from "../components/chat/ConversationPane";
import { SessionRail } from "../components/SessionRail";

export function ChatPage() {
  const navigate = useNavigate();
  const workspace = useChatWorkspace({ navigate });

  if (!workspace.me && workspace.loadingBootstrap) {
    return (
      <main className="page-shell">
        <section className="hero-card">
          <div className="hero-copy">
            <span className="section-kicker">工作区启动中</span>
            <h1>正在加载聊天界面...</h1>
            <p>会话列表、草稿状态和当前对话正在同步。</p>
          </div>
        </section>
      </main>
    );
  }

  if (!workspace.me) {
    return null;
  }

  const hasUploadingAttachments = workspace.selectedDraft.pendingUploads.some((item) => item.status === "uploading");
  const hasReadyUploads = workspace.selectedDraft.pendingUploads.some((item) => item.status === "ready");
  const canSend = Boolean(workspace.selectedDraft.text.trim() || hasReadyUploads);

  return (
    <main className="page-shell chat-shell">
      <ChatTopbar
        accounts={workspace.accounts}
        draft={workspace.selectedDraft}
        me={workspace.me}
        onAllowFailoverChange={(value) => workspace.updateDraftSetting("allowFailover", value)}
        onLogout={() => void workspace.handleLogout()}
        onPinnedAccountChange={(value) => workspace.updateDraftSetting("pinnedAccountId", value)}
      />

      <section className="chat-layout">
        <SessionRail
          activities={workspace.sessionActivities}
          isLoading={workspace.loadingBootstrap}
          onDeleteSession={(sessionId) => workspace.handleDeleteSession(sessionId)}
          onRenameSession={(sessionId, title) => workspace.handleRenameSession(sessionId, title)}
          previews={workspace.sessionPreviews}
          selectedSessionId={workspace.selectedSessionId}
          sessions={workspace.sessions}
          onCreateSession={() => {
            void workspace.handleCreateSession(true).catch(() => undefined);
          }}
          onSelectSession={(sessionId) => {
            void workspace.handleSelectSession(sessionId).catch(() => undefined);
          }}
        />

        <ConversationPane
          activity={workspace.selectedActivity}
          canSend={canSend}
          draft={workspace.selectedDraft}
          hasReadyUploads={hasReadyUploads}
          hasUploadingAttachments={hasUploadingAttachments}
          messages={workspace.selectedMessages}
          preview={workspace.selectedSessionId ? workspace.sessionPreviews[workspace.selectedSessionId] : undefined}
          session={workspace.selectedSession}
          viewportRef={workspace.messageViewportRef}
          onBrowseUploads={() => workspace.fileInputRef.current?.click()}
          onCancelStream={() => workspace.cancelStreaming()}
          onComposerChange={(value) => workspace.setComposerValue(value)}
          onDropFiles={(files) => workspace.enqueueFiles(files)}
          onJumpToLatest={workspace.jumpToLatest}
          onRemoveUpload={(localId) => workspace.removeUpload(localId)}
          onRetryUpload={(localId) => workspace.retryUpload(localId)}
          onScroll={workspace.handleViewportScroll}
          onSend={() => {
            void workspace.sendMessageForSelectedSession();
          }}
          onToggleStream={(value) => workspace.updateDraftSetting("streamEnabled", value)}
          onToggleTemporary={(value) => workspace.updateDraftSetting("temporaryMode", value)}
        />
      </section>

      <input
        accept=".png,.jpg,.jpeg,.webp,.pdf,.pptx"
        hidden
        multiple
        onChange={(event) => {
          if (event.target.files?.length) {
            workspace.enqueueFiles(event.target.files);
            event.target.value = "";
          }
        }}
        ref={workspace.fileInputRef}
        type="file"
      />
    </main>
  );
}
