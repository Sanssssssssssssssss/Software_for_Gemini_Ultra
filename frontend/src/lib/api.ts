export type UiRole = "admin" | "user" | null;

export type UiMe = {
  authenticated: boolean;
  subject: string | null;
  role: UiRole;
  is_admin: boolean;
};

export type SetupCheck = {
  name: string;
  status: "pass" | "warn" | "fail";
  detail: string;
  action?: string | null;
};

export type SetupStatus = {
  status: "ready" | "needs_setup";
  setup_complete: boolean;
  checks: SetupCheck[];
  next_steps: string[];
};

export type AccountSummary = {
  account_id: string;
  state: string;
  account_status: string | null;
  status_description: string | null;
  models: string[];
  active_requests: number;
  queue_depth: number;
  configured_max_concurrency: number;
  cooldown_until: string | null;
  last_error: string | null;
  recent_errors: string[];
  failure_count: number;
  last_transition_at: string | null;
  state_reason: string | null;
};

export type SessionSummary = {
  session_id: string;
  account_id: string;
  routing_policy: string;
  status: string;
  title?: string | null;
  model: string | null;
  gem: string | null;
  allow_failover: boolean;
  gemini_metadata: string[];
  created_at: string | null;
  updated_at: string | null;
};

export type SessionHistoryItem = {
  role: "user" | "assistant" | "system";
  content: string;
  parts: MessageResponsePart[];
  media: MessageMedia[];
  created_at: string | null;
  idempotency_key: string | null;
};

export type SessionHistoryResponse = {
  session_id: string;
  items: SessionHistoryItem[];
};

export type MessageResponse = {
  session_id: string;
  account_id: string;
  content: string;
  parts: MessageResponsePart[];
  media: MessageMedia[];
  cached: boolean;
  message_id: string;
  user_message_id: string | null;
  gemini_metadata: string[];
  created_at: string | null;
};

export type AssetSummary = {
  asset_id: string;
  owner_subject: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  status: string;
  storage_backend: string;
  provider_ref?: string | null;
  created_at?: string | null;
  expires_at?: string | null;
  metadata?: Record<string, unknown>;
};

export type MessageResponsePart =
  | { type: "text"; text: string | null; asset?: null }
  | { type: "asset"; text?: null; asset: AssetSummary | null };

export type MessageMedia = {
  asset_id: string;
  mime_type: string;
  filename: string;
  media_type: "image";
  content_url?: string;
};

export type ChatBootstrap = {
  accounts: AccountSummary[];
  sessions: SessionSummary[];
  is_admin: boolean;
};

export type AdminOverview = {
  accounts: AccountSummary[];
  sessions: SessionSummary[];
  assets: Array<{
    asset_id: string;
    owner_subject: string;
    filename: string;
    mime_type: string;
    size_bytes: number;
    status: string;
    created_at: string | null;
    expires_at: string | null;
  }>;
  telemetry: {
    total_requests: number;
    total_errors: number;
    active_requests: number;
    account_ready: number;
    account_total: number;
    chat_sessions: number;
    chat_messages: number;
    chat_batches: number;
    chat_assets: number;
    batch_workers_active: number;
    session_failovers_total: number;
    account_state_counts: Record<string, number>;
    account_queue_depth: Record<string, number>;
    account_in_flight: Record<string, number>;
    asset_status_counts: Record<string, number>;
    asset_cleanup_runs_total: number;
    asset_expired_total: number;
    asset_deleted_total: number;
  };
};

export type AdminManagedAccount = {
  account_id: string;
  enabled: boolean;
  provider_backend: string;
  cookie_source_browser: string | null;
  cookie_source_browser_path: string | null;
  cookie_source_profile_dir: string | null;
  proxy: string | null;
  max_concurrency: number;
  cooldown_seconds: number;
  request_timeout_seconds: number;
  verify_ssl: boolean;
  tags: string[];
  has_cookie_bundle: boolean;
  last_recovery_at: string | null;
  last_recovery_source: string | null;
  runtime: AccountSummary | null;
};

export type AdminReauthJob = {
  job_id: string;
  account_id: string;
  status:
    | "queued"
    | "launching_browser"
    | "awaiting_login"
    | "collecting_cookies"
    | "validating_provider"
    | "completed"
    | "failed"
    | "cancelled";
  detail: string;
  browser: string | null;
  profile_dir: string | null;
  launched: boolean;
  monitoring: boolean;
  can_complete: boolean;
  is_terminal: boolean;
  created_at: string;
  updated_at: string;
  action_required: string | null;
  launch_url: string | null;
  result: Record<string, unknown>;
};

export type AdminDashboard = {
  accounts: AccountSummary[];
  inventory_accounts: AdminManagedAccount[];
  sessions: SessionSummary[];
  assets: AssetSummary[];
  reauth_jobs: AdminReauthJob[];
  telemetry: AdminOverview["telemetry"];
  health: {
    ready_accounts: number;
    inventory_count: number;
    queue_depth: number;
    reauth_required: number;
    blocked: number;
    cooling_down: number;
    unavailable: number;
  };
};

export type AdminActionResponse = {
  account_id: string;
  action: string;
  state: string;
  detail: string;
};

type ApiErrorPayload = {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
};

export class ApiError extends Error {
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly status: number;

  constructor(status: number, code: string, message: string, details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details ?? {};
  }
}

async function readResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => null)) as ApiErrorPayload | T | null;
  if (!response.ok) {
    const apiError = (payload as ApiErrorPayload | null)?.error;
    throw new ApiError(
      response.status,
      apiError?.code ?? "request_failed",
      apiError?.message ?? `Request failed with HTTP ${response.status}`,
      apiError?.details,
    );
  }
  return payload as T;
}

async function requestJson<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    credentials: "same-origin",
    ...init,
  });
  return readResponse<T>(response);
}

export async function getMe(): Promise<UiMe> {
  return requestJson<UiMe>("/ui/api/me");
}

export async function login(username: string, password: string) {
  return requestJson<UiMe & { redirect_to: string }>("/ui/api/login", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ username, password }),
  });
}

export async function logout() {
  return requestJson<{ ok: true }>("/ui/api/logout", {
    method: "POST",
  });
}

export async function getSetupStatus(): Promise<SetupStatus> {
  return requestJson<SetupStatus>("/ui/api/setup/status");
}

export async function getChatBootstrap(): Promise<ChatBootstrap> {
  return requestJson<ChatBootstrap>("/ui/api/bootstrap");
}

export async function createSession(payload: {
  account_id?: string | null;
  routing_policy?: string;
  allow_failover?: boolean;
  model?: string | null;
  gem?: string | null;
  metadata?: Record<string, unknown>;
}): Promise<SessionSummary> {
  return requestJson<SessionSummary>("/ui/api/sessions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function getSession(sessionId: string): Promise<SessionSummary> {
  return requestJson<SessionSummary>(`/ui/api/sessions/${sessionId}`);
}

export async function updateSession(sessionId: string, payload: { title?: string | null }) {
  return requestJson<SessionSummary>(`/ui/api/sessions/${sessionId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function deleteSession(sessionId: string) {
  return requestJson<{ ok: true; session_id: string }>(`/ui/api/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

export async function getSessionHistory(sessionId: string): Promise<SessionHistoryResponse> {
  return requestJson<SessionHistoryResponse>(`/ui/api/sessions/${sessionId}/history`);
}

export async function sendMessage(payload: {
  session_id: string;
  message?: string | null;
  parts?: Array<{ type: "text"; text: string } | { type: "asset"; asset_id: string }>;
  stream?: boolean;
  temporary?: boolean | null;
  idempotency_key?: string | null;
}): Promise<MessageResponse> {
  return requestJson<MessageResponse>("/ui/api/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function streamMessage(payload: {
  session_id: string;
  message?: string | null;
  parts?: Array<{ type: "text"; text: string } | { type: "asset"; asset_id: string }>;
  stream?: boolean;
  temporary?: boolean | null;
  idempotency_key?: string | null;
}, signal?: AbortSignal): Promise<Response> {
  const response = await fetch("/ui/api/messages:stream", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    await readResponse(response);
  }
  return response;
}

export async function getAdminOverview(): Promise<AdminOverview> {
  return requestJson<AdminOverview>("/ui/api/admin/overview");
}

export async function getAdminDashboard(): Promise<AdminDashboard> {
  return requestJson<AdminDashboard>("/ui/api/admin/dashboard");
}

export async function runAdminAction(
  accountId: string,
  action: string,
): Promise<AdminActionResponse> {
  return requestJson<AdminActionResponse>(`/ui/api/admin/accounts/${accountId}/actions/${action}`, {
    method: "POST",
  });
}

export async function upsertAdminAccount(payload: {
  account_id: string;
  enabled: boolean;
  provider_backend?: string;
  cookie_source_browser?: string | null;
  cookie_source_browser_path?: string | null;
  cookie_source_profile_dir?: string | null;
  proxy?: string | null;
  max_concurrency: number;
  cooldown_seconds: number;
  request_timeout_seconds: number;
  verify_ssl: boolean;
  tags: string[];
  secure_1psid?: string | null;
  secure_1psidts?: string | null;
}): Promise<AdminManagedAccount> {
  return requestJson<AdminManagedAccount>("/ui/api/admin/accounts", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminAccount(accountId: string): Promise<{ ok: true; account_id: string }> {
  return requestJson<{ ok: true; account_id: string }>(`/ui/api/admin/accounts/${accountId}`, {
    method: "DELETE",
  });
}

export async function startAdminReauth(accountId: string): Promise<AdminReauthJob> {
  return requestJson<AdminReauthJob>(`/ui/api/admin/accounts/${accountId}/reauth`, {
    method: "POST",
  });
}

export async function completeAdminReauth(jobId: string): Promise<AdminReauthJob> {
  return requestJson<AdminReauthJob>(`/ui/api/admin/reauth-jobs/${jobId}/complete`, {
    method: "POST",
  });
}

export async function cancelAdminReauth(jobId: string): Promise<AdminReauthJob> {
  return requestJson<AdminReauthJob>(`/ui/api/admin/reauth-jobs/${jobId}/cancel`, {
    method: "POST",
  });
}

export async function listAdminReauthJobs(): Promise<{ items: AdminReauthJob[] }> {
  return requestJson<{ items: AdminReauthJob[] }>("/ui/api/admin/reauth-jobs");
}

export function buildAdminSessionExportUrl(
  sessionId: string,
  format: "json" | "markdown" = "json",
) {
  return `/ui/api/admin/sessions/${sessionId}/export?format=${format}`;
}

export function buildAdminSessionsExportUrl(options?: {
  format?: "json" | "markdown";
  owner_subject?: string;
  account_id?: string;
  limit?: number;
}) {
  const params = new URLSearchParams();
  params.set("format", options?.format ?? "json");
  if (options?.owner_subject) {
    params.set("owner_subject", options.owner_subject);
  }
  if (options?.account_id) {
    params.set("account_id", options.account_id);
  }
  if (typeof options?.limit === "number") {
    params.set("limit", String(options.limit));
  }
  return `/ui/api/admin/sessions/export?${params.toString()}`;
}

export function buildUiAssetContentUrl(assetId: string) {
  return `/ui/api/assets/${assetId}/content`;
}

export async function uploadAsset(
  file: File,
  options?: {
    temporary?: boolean;
    onProgress?: (progress: number) => void;
    signal?: AbortSignal;
  },
): Promise<{ asset: AssetSummary }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/ui/api/uploads");
    xhr.withCredentials = true;

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable || !options?.onProgress) {
        return;
      }
      options.onProgress(Math.round((event.loaded / event.total) * 100));
    };

    xhr.onerror = () => {
      reject(new ApiError(0, "upload_failed", "The upload request failed."));
    };

    xhr.onload = () => {
      try {
        const payload = JSON.parse(xhr.responseText ?? "{}") as { asset?: AssetSummary; error?: { code?: string; message?: string; details?: Record<string, unknown> } };
        if (xhr.status >= 200 && xhr.status < 300 && payload.asset) {
          resolve({ asset: payload.asset });
          return;
        }
        reject(
          new ApiError(
            xhr.status,
            payload.error?.code ?? "upload_failed",
            payload.error?.message ?? `Upload failed with HTTP ${xhr.status}`,
            payload.error?.details,
          ),
        );
      } catch (error) {
        reject(error instanceof Error ? error : new Error("Upload response could not be parsed."));
      }
    };

    options?.signal?.addEventListener("abort", () => {
      xhr.abort();
      reject(new DOMException("Upload aborted", "AbortError"));
    });

    const formData = new FormData();
    formData.append("file", file);
    formData.append("temporary", String(options?.temporary ?? true));
    xhr.send(formData);
  });
}
