import type { PendingUpload, SessionActivity, SessionDraft } from "../../chat/types";

type ComposerDockProps = {
  activity: SessionActivity;
  canSend: boolean;
  draft: SessionDraft;
  hasReadyUploads: boolean;
  hasUploadingAttachments: boolean;
  onBrowseUploads: () => void;
  onCancel: () => void;
  onChange: (value: string) => void;
  onDropFiles: (files: FileList | File[]) => void;
  onPasteFiles: (files: File[]) => void;
  onRemoveUpload: (localId: string) => void;
  onRetryUpload: (localId: string) => void;
  onSend: () => void;
  onToggleStream: (value: boolean) => void;
  onToggleTemporary: (value: boolean) => void;
  uploads: PendingUpload[];
};

export function ComposerDock({
  activity,
  canSend,
  draft,
  hasReadyUploads,
  hasUploadingAttachments,
  onBrowseUploads,
  onCancel,
  onChange,
  onDropFiles,
  onPasteFiles,
  onRemoveUpload,
  onRetryUpload,
  onSend,
  onToggleStream,
  onToggleTemporary,
  uploads,
}: ComposerDockProps) {
  return (
    <div
      className={`composer-dock${hasUploadingAttachments ? " is-uploading" : ""}`}
      data-testid="upload-dropzone"
      onDragOver={(event) => {
        event.preventDefault();
      }}
      onDrop={(event) => {
        event.preventDefault();
        if (event.dataTransfer.files?.length) {
          onDropFiles(event.dataTransfer.files);
        }
      }}
    >
      <label className="field composer-field">
        <textarea
          className="composer-input"
          data-testid="chat-composer"
          onChange={(event) => onChange(event.target.value)}
          onPaste={(event) => {
            const items = Array.from(event.clipboardData?.items || []);
            const imageFiles = items
              .map((item) => (item.kind === "file" ? item.getAsFile() : null))
              .filter((item): item is File => !!item && item.type.startsWith("image/"));

            if (imageFiles.length) {
              event.preventDefault();
              onPasteFiles(imageFiles);
            }
          }}
          placeholder="输入消息，或把图片 / PDF / PPTX 直接拖到这里。其他会话回复时，你也可以继续切换和发送。"
          value={draft.text}
        />
      </label>

      {uploads.length ? (
        <div className="composer-upload-strip" data-testid="upload-queue">
          {uploads.map((upload) => (
            <article className={`composer-upload-chip status-${upload.status}`} key={upload.localId}>
              {upload.previewUrl ? (
                <img alt={upload.file.name} className="composer-upload-chip__preview" src={upload.previewUrl} />
              ) : (
                <span className="composer-upload-chip__glyph">{upload.file.type.startsWith("image/") ? "图" : "文"}</span>
              )}
              <div className="composer-upload-chip__meta">
                <strong>{upload.asset?.filename || upload.file.name}</strong>
                <span>
                  {upload.status === "uploading"
                    ? `上传中 ${upload.progress}%`
                    : upload.status === "ready"
                      ? "待发送"
                      : upload.error || "上传已取消"}
                </span>
              </div>
              <div className="composer-upload-chip__actions">
                {upload.status === "error" || upload.status === "cancelled" ? (
                  <button className="secondary-link compact-link button-reset" type="button" onClick={() => onRetryUpload(upload.localId)}>
                    重试
                  </button>
                ) : null}
                <button className="secondary-link compact-link button-reset" type="button" onClick={() => onRemoveUpload(upload.localId)}>
                  {upload.status === "uploading" ? "取消" : "移除"}
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : null}

      <div className="composer-footer">
        <div className="composer-left-controls">
          <button
            aria-label="添加文件"
            className="composer-plus button-reset"
            type="button"
            onClick={onBrowseUploads}
          >
            +
          </button>
          <label className="toggle-chip">
            <input
              checked={draft.streamEnabled}
              onChange={(event) => onToggleStream(event.target.checked)}
              type="checkbox"
            />
            <span>流式回复</span>
          </label>
          <label className="toggle-chip">
            <input
              checked={draft.temporaryMode}
              onChange={(event) => onToggleTemporary(event.target.checked)}
              type="checkbox"
            />
            <span>临时消息</span>
          </label>
          {hasReadyUploads ? <span className="composer-pill">已准备 {draft.pendingUploads.length} 个附件</span> : null}
          {activity.phaseLabel ? <span className="composer-pill">{activity.phaseLabel}</span> : null}
        </div>

        <div className="composer-actions">
          {activity.isStreaming ? (
            <button className="secondary-link compact-link button-reset" type="button" onClick={onCancel}>
              停止生成
            </button>
          ) : null}
          <button
            className="primary-button"
            data-testid="chat-send"
            disabled={!canSend || activity.isSending || hasUploadingAttachments}
            type="button"
            onClick={onSend}
          >
            {activity.isSending ? "发送中..." : "发送"}
          </button>
        </div>
      </div>
    </div>
  );
}
