import type { AssetSummary, MessageMedia, MessageResponsePart } from "../lib/api";
import { buildUiAssetContentUrl } from "../lib/api";

type MessageAttachmentsProps = {
  parts?: MessageResponsePart[];
  media?: MessageMedia[];
};

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function assetKind(asset: AssetSummary) {
  if (asset.mime_type.startsWith("image/")) return "图片";
  if (asset.mime_type === "application/pdf") return "PDF";
  if (asset.mime_type.includes("presentationml.presentation")) return "PPTX";
  return "文件";
}

export function MessageAttachments({ parts = [], media = [] }: MessageAttachmentsProps) {
  const attachments = parts
    .filter((part): part is Extract<MessageResponsePart, { type: "asset" }> => part.type === "asset" && !!part.asset)
    .map((part) => part.asset as AssetSummary);

  const imageMedia = new Map(
    media.filter((item) => item.media_type === "image").map((item) => [item.asset_id, item]),
  );

  const imageItems = [
    ...media.filter((item) => item.media_type === "image"),
    ...attachments
      .filter((asset) => asset.mime_type.startsWith("image/") && !imageMedia.has(asset.asset_id))
      .map((asset) => ({
        asset_id: asset.asset_id,
        filename: asset.filename,
        mime_type: asset.mime_type,
        media_type: "image" as const,
        content_url: buildUiAssetContentUrl(asset.asset_id),
      })),
  ];

  const fileItems = attachments.filter(
    (asset) => !(asset.mime_type.startsWith("image/") && imageMedia.has(asset.asset_id)),
  );

  if (!imageItems.length && !fileItems.length) {
    return null;
  }

  return (
    <div className="message-attachments">
      {imageItems.length ? (
        <div className="message-media-grid">
          {imageItems.map((item) => {
            const href = item.content_url || buildUiAssetContentUrl(item.asset_id);
            return (
              <a className="generated-media-card generated-media-card--large" href={href} key={item.asset_id} rel="noreferrer" target="_blank">
                <img alt={item.filename} className="attachment-image attachment-image--large" src={href} />
                <span>{item.filename}</span>
              </a>
            );
          })}
        </div>
      ) : null}

      {fileItems.map((asset) => (
        <article className="attachment-card attachment-card--file" key={asset.asset_id}>
          <div className="attachment-card__meta">
            <strong>{asset.filename}</strong>
            <span>{assetKind(asset)}</span>
            <span>{formatBytes(asset.size_bytes)}</span>
          </div>
          <a className="attachment-file-link" href={buildUiAssetContentUrl(asset.asset_id)} rel="noreferrer" target="_blank">
            打开附件
          </a>
        </article>
      ))}
    </div>
  );
}
