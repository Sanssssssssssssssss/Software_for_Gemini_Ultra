import type { AssetSummary, MessageMedia, MessageResponsePart } from "../lib/api";
import { buildUiAssetContentUrl } from "../lib/api";

type MessageAttachmentsProps = {
  parts?: MessageResponsePart[];
  media?: MessageMedia[];
};

function formatBytes(value: number) {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function assetKind(asset: AssetSummary) {
  if (asset.mime_type.startsWith("image/")) {
    return "Image";
  }
  if (asset.mime_type === "application/pdf") {
    return "PDF";
  }
  if (asset.mime_type.includes("presentationml.presentation")) {
    return "PPTX";
  }
  return "File";
}

export function MessageAttachments({ parts = [], media = [] }: MessageAttachmentsProps) {
  const attachments = parts
    .filter((part): part is Extract<MessageResponsePart, { type: "asset" }> => part.type === "asset" && !!part.asset)
    .map((part) => part.asset as AssetSummary);

  if (!attachments.length && !media.length) {
    return null;
  }

  return (
    <div className="message-attachments">
      {attachments.map((asset) => {
        const isImage = asset.mime_type.startsWith("image/");
        return (
          <article className="attachment-card" key={asset.asset_id}>
            <div className="attachment-card__meta">
              <strong>{asset.filename}</strong>
              <span>{assetKind(asset)}</span>
              <span>{formatBytes(asset.size_bytes)}</span>
            </div>
            {isImage ? (
              <a className="attachment-image-link" href={buildUiAssetContentUrl(asset.asset_id)} target="_blank" rel="noreferrer">
                <img alt={asset.filename} className="attachment-image" src={buildUiAssetContentUrl(asset.asset_id)} />
              </a>
            ) : (
              <a className="attachment-file-link" href={buildUiAssetContentUrl(asset.asset_id)} target="_blank" rel="noreferrer">
                Open attachment
              </a>
            )}
          </article>
        );
      })}
      {media
        .filter((item) => item.media_type === "image")
        .map((item) => (
          <a
            className="generated-media-card"
            href={buildUiAssetContentUrl(item.asset_id)}
            key={item.asset_id}
            rel="noreferrer"
            target="_blank"
          >
            <img alt={item.filename} className="attachment-image" src={buildUiAssetContentUrl(item.asset_id)} />
            <span>{item.filename}</span>
          </a>
        ))}
    </div>
  );
}
