import type { RefObject } from "react";

import type { SessionActivity, SessionDraft, SessionPreview, UiMessage } from "../../chat/types";
import type { SessionSummary } from "../../lib/api";
import { ConversationHeader } from "./ConversationHeader";
import { ComposerDock } from "./ComposerDock";
import { MessageViewport } from "./MessageViewport";

type ConversationPaneProps = {
  activity: SessionActivity;
  canSend: boolean;
  draft: SessionDraft;
  hasReadyUploads: boolean;
  hasUploadingAttachments: boolean;
  messages: UiMessage[];
  onBrowseUploads: () => void;
  onCancelStream: () => void;
  onComposerChange: (value: string) => void;
  onDropFiles: (files: FileList | File[]) => void;
  onJumpToLatest: () => void;
  onRemoveUpload: (localId: string) => void;
  onRetryUpload: (localId: string) => void;
  onScroll: () => void;
  onSend: () => void;
  onToggleStream: (value: boolean) => void;
  onToggleTemporary: (value: boolean) => void;
  preview?: SessionPreview;
  session: SessionSummary | null;
  viewportRef: RefObject<HTMLDivElement>;
};

export function ConversationPane({
  activity,
  canSend,
  draft,
  hasReadyUploads,
  hasUploadingAttachments,
  messages,
  onBrowseUploads,
  onCancelStream,
  onComposerChange,
  onDropFiles,
  onJumpToLatest,
  onRemoveUpload,
  onRetryUpload,
  onScroll,
  onSend,
  onToggleStream,
  onToggleTemporary,
  preview,
  session,
  viewportRef,
}: ConversationPaneProps) {
  return (
    <section className="conversation-pane panel-surface">
      <ConversationHeader preview={preview} session={session} />

      {activity.status ? <div className={`inline-banner tone-${activity.status.tone}`}>{activity.status.message}</div> : null}

      <MessageViewport
        isAtBottom={activity.isAtBottom}
        isLoading={activity.loadingHistory}
        messages={messages}
        onJumpToLatest={onJumpToLatest}
        onScroll={onScroll}
        viewportRef={viewportRef}
      />

      <div className="conversation-dock">
        <ComposerDock
          activity={activity}
          canSend={canSend}
          draft={draft}
          hasReadyUploads={hasReadyUploads}
          hasUploadingAttachments={hasUploadingAttachments}
          onBrowseUploads={onBrowseUploads}
          onCancel={onCancelStream}
          onChange={onComposerChange}
          onDropFiles={onDropFiles}
          onPasteFiles={(files) => onDropFiles(files)}
          onRemoveUpload={onRemoveUpload}
          onRetryUpload={onRetryUpload}
          onSend={onSend}
          onToggleStream={onToggleStream}
          onToggleTemporary={onToggleTemporary}
          uploads={draft.pendingUploads}
        />
      </div>
    </section>
  );
}
