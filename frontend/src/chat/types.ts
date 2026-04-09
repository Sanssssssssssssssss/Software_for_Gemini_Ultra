import type { AssetSummary, MessageMedia, MessageResponsePart } from "../lib/api";

export const DRAFT_SESSION_KEY = "__draft__";

export type StatusTone = "info" | "error" | "success";

export type ChatStatus = {
  tone: StatusTone;
  message: string;
} | null;

export type PendingUploadStatus = "uploading" | "ready" | "error" | "cancelled";

export type PendingUpload = {
  localId: string;
  file: File;
  status: PendingUploadStatus;
  progress: number;
  asset?: AssetSummary;
  error?: string;
  previewUrl?: string;
  abortController?: AbortController | null;
};

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

export type SessionPreview = {
  title?: string;
  preview?: string;
};

export type SessionDraft = {
  text: string;
  streamEnabled: boolean;
  temporaryMode: boolean;
  allowFailover: boolean;
  pinnedAccountId: string;
  pendingUploads: PendingUpload[];
};

export type SessionActivity = {
  loadingHistory: boolean;
  isSending: boolean;
  isStreaming: boolean;
  isAtBottom: boolean;
  phaseLabel: string | null;
  status: ChatStatus;
};
