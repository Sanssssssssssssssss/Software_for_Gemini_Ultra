import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AppHeader } from "../components/AppHeader";
import {
  type AccountSummary,
  type AdminDashboard,
  type AdminManagedAccount,
  type AdminReauthJob,
  ApiError,
  buildAdminSessionExportUrl,
  buildAdminSessionsExportUrl,
  cancelAdminReauth,
  completeAdminReauth,
  getAdminDashboard,
  getMe,
  logout,
  runAdminAction,
  startAdminReauth,
  type UiMe,
  upsertAdminAccount,
} from "../lib/api";

type AccountFormState = {
  account_id: string;
  enabled: boolean;
  cookie_source_browser: string;
  cookie_source_browser_path: string;
  cookie_source_profile_dir: string;
  proxy: string;
  max_concurrency: number;
  cooldown_seconds: number;
  request_timeout_seconds: number;
  verify_ssl: boolean;
  tags: string;
};

const DEFAULT_ACCOUNT_FORM: AccountFormState = {
  account_id: "",
  enabled: true,
  cookie_source_browser: "chrome",
  cookie_source_browser_path: "",
  cookie_source_profile_dir: "",
  proxy: "",
  max_concurrency: 1,
  cooldown_seconds: 60,
  request_timeout_seconds: 450,
  verify_ssl: true,
  tags: "",
};

const ADMIN_ACTIONS = [
  { key: "refresh", label: "刷新账号" },
  { key: "clear-cooldown", label: "清除冷却" },
  { key: "mark-reauth-required", label: "标记需重登" },
  { key: "disable-runtime", label: "运行时禁用" },
  { key: "enable-runtime", label: "运行时启用" },
] as const;

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return "-";
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

function formatAccountState(state: string) {
  switch (state) {
    case "ready":
      return "READY";
    case "busy":
      return "BUSY";
    case "degraded":
      return "DEGRADED";
    case "cooling_down":
      return "COOLDOWN";
    case "reauth_required":
      return "REAUTH";
    case "blocked":
      return "BLOCKED";
    case "unavailable":
      return "OFFLINE";
    case "disabled":
      return "DISABLED";
    default:
      return state.toUpperCase();
  }
}

function formatRuntimeStatus(status: string | null) {
  if (!status) {
    return "UNKNOWN";
  }
  return status.toUpperCase();
}

function toFormState(account?: AdminManagedAccount | null): AccountFormState {
  if (!account) {
    return { ...DEFAULT_ACCOUNT_FORM };
  }
  return {
    account_id: account.account_id,
    enabled: account.enabled,
    cookie_source_browser: account.cookie_source_browser ?? "chrome",
    cookie_source_browser_path: account.cookie_source_browser_path ?? "",
    cookie_source_profile_dir: account.cookie_source_profile_dir ?? "",
    proxy: account.proxy ?? "",
    max_concurrency: account.max_concurrency,
    cooldown_seconds: account.cooldown_seconds,
    request_timeout_seconds: account.request_timeout_seconds,
    verify_ssl: account.verify_ssl,
    tags: account.tags.join(", "),
  };
}

function openDownload(url: string) {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export function AdminPage() {
  const navigate = useNavigate();
  const [me, setMe] = useState<UiMe | null>(null);
  const [dashboard, setDashboard] = useState<AdminDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pendingActions, setPendingActions] = useState<Record<string, boolean>>({});
  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null);
  const [accountForm, setAccountForm] = useState<AccountFormState>({ ...DEFAULT_ACCOUNT_FORM });

  async function refreshDashboard() {
    try {
      const next = await getAdminDashboard();
      setDashboard(next);
      if (selectedAccountId) {
        const selected = next.inventory_accounts.find((item) => item.account_id === selectedAccountId);
        if (selected) {
          setAccountForm(toFormState(selected));
        }
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "刷新管理数据失败。");
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const currentMe = await getMe();
        if (!currentMe.authenticated) {
          navigate("/ui/login", { replace: true });
          return;
        }
        if (!currentMe.is_admin) {
          navigate("/ui/chat", { replace: true });
          return;
        }
        const nextDashboard = await getAdminDashboard();
        if (cancelled) {
          return;
        }
        setMe(currentMe);
        setDashboard(nextDashboard);
      } catch (caught) {
        if (cancelled) {
          return;
        }
        if (caught instanceof ApiError && caught.status === 401) {
          navigate("/ui/login", { replace: true });
          return;
        }
        setError(caught instanceof Error ? caught.message : "加载管理控制台失败。");
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();
    const interval = window.setInterval(() => {
      void refreshDashboard();
    }, 15000);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [navigate, selectedAccountId]);

  const selectedAccount = useMemo(() => {
    if (!dashboard || !selectedAccountId) {
      return null;
    }
    return dashboard.inventory_accounts.find((item) => item.account_id === selectedAccountId) ?? null;
  }, [dashboard, selectedAccountId]);

  async function handleLogout() {
    await logout();
    navigate("/ui/login", { replace: true });
  }

  async function handleRuntimeAction(account: AccountSummary, action: string) {
    const key = `${account.account_id}:${action}`;
    setPendingActions((current) => ({ ...current, [key]: true }));
    setError("");
    setNotice("");
    try {
      const result = await runAdminAction(account.account_id, action);
      setNotice(`${account.account_id}：${result.detail}`);
      await refreshDashboard();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "执行账号动作失败。");
    } finally {
      setPendingActions((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  }

  async function handleStartReauth(accountId: string) {
    setError("");
    setNotice("");
    try {
      const job = await startAdminReauth(accountId);
      setNotice(`${accountId}：已启动浏览器重登流程，请在本机完成登录后点击“完成同步”。`);
      await refreshDashboard();
      if (job.launch_url) {
        console.info("Reauth launch URL", job.launch_url);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "启动重登流程失败。");
    }
  }

  async function handleCompleteReauth(job: AdminReauthJob) {
    const key = `job:${job.job_id}:complete`;
    setPendingActions((current) => ({ ...current, [key]: true }));
    setError("");
    setNotice("");
    try {
      const result = await completeAdminReauth(job.job_id);
      setNotice(`${result.account_id}：${result.detail}`);
      await refreshDashboard();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "完成同步失败。");
    } finally {
      setPendingActions((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  }

  async function handleCancelReauth(job: AdminReauthJob) {
    const key = `job:${job.job_id}:cancel`;
    setPendingActions((current) => ({ ...current, [key]: true }));
    setError("");
    setNotice("");
    try {
      const result = await cancelAdminReauth(job.job_id);
      setNotice(`${result.account_id}：${result.detail}`);
      await refreshDashboard();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "取消重登流程失败。");
    } finally {
      setPendingActions((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  }

  async function handleSaveAccount() {
    setError("");
    setNotice("");
    try {
      const saved = await upsertAdminAccount({
        account_id: accountForm.account_id.trim(),
        enabled: accountForm.enabled,
        provider_backend: "gemini_web",
        cookie_source_browser: accountForm.cookie_source_browser || null,
        cookie_source_browser_path: accountForm.cookie_source_browser_path || null,
        cookie_source_profile_dir: accountForm.cookie_source_profile_dir || null,
        proxy: accountForm.proxy || null,
        max_concurrency: Number(accountForm.max_concurrency),
        cooldown_seconds: Number(accountForm.cooldown_seconds),
        request_timeout_seconds: Number(accountForm.request_timeout_seconds),
        verify_ssl: accountForm.verify_ssl,
        tags: accountForm.tags
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      });
      setSelectedAccountId(saved.account_id);
      setAccountForm(toFormState(saved));
      setNotice(`账号 ${saved.account_id} 已保存，运行时库存已同步。`);
      await refreshDashboard();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存账号失败。");
    }
  }

  if (!me && loading) {
    return (
      <main className="page-shell">
        <section className="hero-card hero-card--terminal">
          <div className="hero-copy">
            <span className="section-kicker">管理控制台</span>
            <h1>正在装载系统概览…</h1>
            <p>账号状态、重登任务、会话导出和资产活动正在同步。</p>
          </div>
        </section>
      </main>
    );
  }

  if (!me || !dashboard) {
    return null;
  }

  return (
    <main className="page-shell admin-shell">
      <AppHeader
        me={me}
        badge="Admin Control"
        title="账号恢复与系统控制台"
        subtitle="把账号库存、重登恢复、会话导出和系统健康放到一个地方处理。普通用户不能访问这里，管理员可以直接在本机拉起浏览器完成 Gemini 账号重登。"
        actions={
          <div className="app-nav">
            <button className="secondary-link compact-link button-reset" type="button" onClick={() => void refreshDashboard()}>
              刷新概览
            </button>
            <button className="secondary-link compact-link button-reset" type="button" onClick={() => void handleLogout()}>
              退出登录
            </button>
          </div>
        }
      />

      <section className="admin-summary-strip">
        <article className="metric-card">
          <span>可路由账号</span>
          <strong>{dashboard.health.ready_accounts}</strong>
          <p>总库存 {dashboard.health.inventory_count} 个</p>
        </article>
        <article className="metric-card danger">
          <span>需重登</span>
          <strong>{dashboard.health.reauth_required}</strong>
          <p>这类账号需要管理员完成浏览器重登</p>
        </article>
        <article className="metric-card">
          <span>进行中请求</span>
          <strong>{dashboard.telemetry.active_requests}</strong>
          <p>全局排队深度 {dashboard.health.queue_depth}</p>
        </article>
        <article className="metric-card">
          <span>会话总数</span>
          <strong>{dashboard.telemetry.chat_sessions}</strong>
          <p>累计消息 {dashboard.telemetry.chat_messages}</p>
        </article>
        <article className="metric-card">
          <span>文件总数</span>
          <strong>{dashboard.telemetry.chat_assets}</strong>
          <p>已清理 {dashboard.telemetry.asset_deleted_total} 个文件</p>
        </article>
      </section>

      {error ? <div className="inline-error">{error}</div> : null}
      {notice ? <div className="inline-banner tone-success">{notice}</div> : null}

      <section className="admin-grid">
        <div className="admin-main">
          <section className="panel-surface admin-panel">
            <div className="section-head">
              <div>
                <span className="section-kicker">账号库存</span>
                <h2>新增账号、编辑资料、拉起重登</h2>
              </div>
              <div className="app-nav">
                <button
                  className="secondary-link compact-link button-reset"
                  type="button"
                  onClick={() => {
                    setSelectedAccountId(null);
                    setAccountForm({ ...DEFAULT_ACCOUNT_FORM });
                  }}
                >
                  新建账号
                </button>
                <button
                  className="secondary-link compact-link button-reset"
                  type="button"
                  onClick={() => void refreshDashboard()}
                >
                  刷新概览
                </button>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.35fr) minmax(320px, 0.92fr)", gap: "var(--space-lg)", alignItems: "start" }}>
              <div className="runtime-account-list">
                {dashboard.inventory_accounts.map((account) => (
                  <article className="runtime-account" key={account.account_id}>
                    <div className="runtime-account__head">
                      <div className="runtime-account__identity">
                        <strong>{account.account_id}</strong>
                        <p>{account.cookie_source_profile_dir || "尚未绑定浏览器 profile"}</p>
                      </div>
                      <span className={`status-pill compact state-badge state-${account.runtime?.state || "disabled"}`}>
                        {formatAccountState(account.runtime?.state || (account.enabled ? "disabled" : "disabled"))}
                      </span>
                    </div>

                    <div className="runtime-account__stats">
                      <div>
                        <span>Gemini 状态</span>
                        <strong>{formatRuntimeStatus(account.runtime?.account_status ?? null)}</strong>
                      </div>
                      <div>
                        <span>并发</span>
                        <strong>
                          {account.runtime?.active_requests ?? 0}/{account.max_concurrency}
                        </strong>
                      </div>
                      <div>
                        <span>队列</span>
                        <strong>{account.runtime?.queue_depth ?? 0}</strong>
                      </div>
                      <div>
                        <span>Cookie</span>
                        <strong>{account.has_cookie_bundle ? "已同步" : "未同步"}</strong>
                      </div>
                    </div>

                    <div className="runtime-account__meta">
                      <span>{account.runtime?.state_reason || "暂无额外说明"}</span>
                      <span>{account.tags.length ? `标签：${account.tags.join(", ")}` : "未配置标签"}</span>
                    </div>

                    <div className="account-admin-card__actions">
                      <button
                        className="secondary-link compact-link button-reset"
                        type="button"
                        onClick={() => {
                          setSelectedAccountId(account.account_id);
                          setAccountForm(toFormState(account));
                        }}
                      >
                        编辑资料
                      </button>
                      <button
                        className="secondary-link compact-link button-reset"
                        type="button"
                        onClick={() => void handleStartReauth(account.account_id)}
                      >
                        一键重登
                      </button>
                      {account.runtime
                        ? ADMIN_ACTIONS.map((action) => {
                            const key = `${account.account_id}:${action.key}`;
                            const isPending = pendingActions[key];
                            return (
                              <button
                                className="secondary-link compact-link button-reset"
                                data-testid={`admin-action-${account.account_id}-${action.key}`}
                                disabled={isPending}
                                key={action.key}
                                type="button"
                                onClick={() => void handleRuntimeAction(account.runtime as AccountSummary, action.key)}
                              >
                                {isPending ? "处理中..." : action.label}
                              </button>
                            );
                          })
                        : null}
                    </div>
                  </article>
                ))}
              </div>

              <section className="panel-surface">
                <div className="section-head">
                  <div>
                    <span className="section-kicker">{selectedAccount ? "编辑账号" : "新建账号"}</span>
                    <h3>{selectedAccount ? selectedAccount.account_id : "账号资料"}</h3>
                  </div>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "var(--space-sm)" }}>
                  <label className="field">
                    <span>账号 ID</span>
                    <input
                      aria-label="账号 ID"
                      value={accountForm.account_id}
                      onChange={(event) => setAccountForm((current) => ({ ...current, account_id: event.target.value }))}
                    />
                  </label>
                  <label className="field">
                    <span>浏览器</span>
                    <select
                      aria-label="浏览器"
                      value={accountForm.cookie_source_browser}
                      onChange={(event) =>
                        setAccountForm((current) => ({ ...current, cookie_source_browser: event.target.value }))
                      }
                    >
                      <option value="chrome">Chrome</option>
                      <option value="edge">Edge</option>
                    </select>
                  </label>
                  <label className="field" style={{ gridColumn: "1 / -1" }}>
                    <span>浏览器 profile 目录</span>
                    <input
                      aria-label="浏览器 profile 目录"
                      placeholder="例如 data/chrome-manual-profile-2"
                      value={accountForm.cookie_source_profile_dir}
                      onChange={(event) =>
                        setAccountForm((current) => ({ ...current, cookie_source_profile_dir: event.target.value }))
                      }
                    />
                  </label>
                  <label className="field" style={{ gridColumn: "1 / -1" }}>
                    <span>浏览器可执行文件路径（可选）</span>
                    <input
                      aria-label="浏览器可执行文件路径（可选）"
                      value={accountForm.cookie_source_browser_path}
                      onChange={(event) =>
                        setAccountForm((current) => ({ ...current, cookie_source_browser_path: event.target.value }))
                      }
                    />
                  </label>
                  <label className="field">
                    <span>最大并发</span>
                    <input
                      aria-label="最大并发"
                      min={1}
                      type="number"
                      value={accountForm.max_concurrency}
                      onChange={(event) =>
                        setAccountForm((current) => ({ ...current, max_concurrency: Number(event.target.value) || 1 }))
                      }
                    />
                  </label>
                  <label className="field">
                    <span>冷却秒数</span>
                    <input
                      aria-label="冷却秒数"
                      min={5}
                      type="number"
                      value={accountForm.cooldown_seconds}
                      onChange={(event) =>
                        setAccountForm((current) => ({ ...current, cooldown_seconds: Number(event.target.value) || 60 }))
                      }
                    />
                  </label>
                  <label className="field">
                    <span>请求超时秒数</span>
                    <input
                      aria-label="请求超时秒数"
                      min={10}
                      type="number"
                      value={accountForm.request_timeout_seconds}
                      onChange={(event) =>
                        setAccountForm((current) => ({
                          ...current,
                          request_timeout_seconds: Number(event.target.value) || 450,
                        }))
                      }
                    />
                  </label>
                  <label className="field">
                    <span>代理（可选）</span>
                    <input
                      aria-label="代理（可选）"
                      value={accountForm.proxy}
                      onChange={(event) => setAccountForm((current) => ({ ...current, proxy: event.target.value }))}
                    />
                  </label>
                  <label className="field" style={{ gridColumn: "1 / -1" }}>
                    <span>标签</span>
                    <input
                      aria-label="标签"
                      placeholder="例如 shared, ultra, cn-team"
                      value={accountForm.tags}
                      onChange={(event) => setAccountForm((current) => ({ ...current, tags: event.target.value }))}
                    />
                  </label>
                </div>

                <div className="stack-sm">
                  <label className="toggle-chip">
                    <input
                      checked={accountForm.enabled}
                      type="checkbox"
                      onChange={(event) => setAccountForm((current) => ({ ...current, enabled: event.target.checked }))}
                    />
                    启用账号
                  </label>
                  <label className="toggle-chip">
                    <input
                      checked={accountForm.verify_ssl}
                      type="checkbox"
                      onChange={(event) =>
                        setAccountForm((current) => ({ ...current, verify_ssl: event.target.checked }))
                      }
                    />
                    校验 SSL
                  </label>
                </div>

                <div className="app-nav">
                  <button className="primary-button button-reset" type="button" onClick={() => void handleSaveAccount()}>
                    保存账号
                  </button>
                  {selectedAccount ? (
                    <button
                      className="secondary-link compact-link button-reset"
                      type="button"
                      onClick={() => void handleStartReauth(selectedAccount.account_id)}
                    >
                      为当前账号启动重登
                    </button>
                  ) : null}
                </div>
              </section>
            </div>
          </section>

          <section className="panel-surface admin-panel">
            <div className="section-head">
              <div>
                <span className="section-kicker">重登任务</span>
                <h2>浏览器登录与 Cookie 同步</h2>
              </div>
            </div>

            <div className="stack-md">
              {dashboard.reauth_jobs.length ? (
                dashboard.reauth_jobs.map((job) => {
                  const completeKey = `job:${job.job_id}:complete`;
                  const cancelKey = `job:${job.job_id}:cancel`;
                  return (
                    <article className="panel-surface" key={job.job_id}>
                      <div className="runtime-account__head">
                        <div className="runtime-account__identity">
                          <strong>{job.account_id}</strong>
                          <p>{job.detail}</p>
                        </div>
                        <span className={`status-pill compact state-badge state-${job.status}`}>{job.status}</span>
                      </div>
                      <div className="runtime-account__meta">
                        <span>{job.browser ? `浏览器：${job.browser}` : "未识别浏览器"}</span>
                        <span>{job.profile_dir || "未配置 profile 目录"}</span>
                      </div>
                      {job.action_required ? <div className="inline-banner tone-info">{job.action_required}</div> : null}
                      <div className="account-admin-card__actions">
                        <button
                          className="primary-button button-reset"
                          disabled={!!pendingActions[completeKey] || job.status === "completed" || job.status === "cancelled"}
                          type="button"
                          onClick={() => void handleCompleteReauth(job)}
                        >
                          {pendingActions[completeKey] ? "同步中..." : "完成同步"}
                        </button>
                        <button
                          className="secondary-link compact-link button-reset"
                          disabled={!!pendingActions[cancelKey] || !job.launched}
                          type="button"
                          onClick={() => void handleCancelReauth(job)}
                        >
                          {pendingActions[cancelKey] ? "取消中..." : "取消任务"}
                        </button>
                      </div>
                    </article>
                  );
                })
              ) : (
                <div className="rail-empty">目前还没有重登任务。管理员点击账号卡片上的“一键重登”后，这里会出现任务状态。</div>
              )}
            </div>
          </section>
        </div>

        <aside className="admin-side">
          <section className="panel-surface admin-panel">
            <div className="section-head">
              <div>
                <span className="section-kicker">会话导出</span>
                <h2>导出当前系统会话</h2>
              </div>
              <div className="app-nav">
                <button
                  className="secondary-link compact-link button-reset"
                  type="button"
                  onClick={() => openDownload(buildAdminSessionsExportUrl({ format: "json", limit: 200 }))}
                >
                  导出全部 JSON
                </button>
                <button
                  className="secondary-link compact-link button-reset"
                  type="button"
                  onClick={() => openDownload(buildAdminSessionsExportUrl({ format: "markdown", limit: 200 }))}
                >
                  导出全部 Markdown
                </button>
              </div>
            </div>
            <div className="admin-session-list">
              {dashboard.sessions.length ? (
                dashboard.sessions.map((session, index) => (
                  <div className="admin-session-row" key={session.session_id}>
                    <div>
                      <strong>{session.title || `会话 ${String(index + 1).padStart(2, "0")}`}</strong>
                      <p>
                        {session.account_id} / {session.routing_policy}
                      </p>
                    </div>
                    <div className="admin-session-row__meta">
                      <span className={`status-pill compact state-badge state-${session.status}`}>
                        {formatAccountState(session.status)}
                      </span>
                      <small>{formatTimestamp(session.updated_at || session.created_at)}</small>
                      <div className="app-nav">
                        <button
                          className="secondary-link compact-link button-reset"
                          type="button"
                          onClick={() => openDownload(buildAdminSessionExportUrl(session.session_id, "json"))}
                        >
                          JSON
                        </button>
                        <button
                          className="secondary-link compact-link button-reset"
                          type="button"
                          onClick={() => openDownload(buildAdminSessionExportUrl(session.session_id, "markdown"))}
                        >
                          Markdown
                        </button>
                      </div>
                    </div>
                  </div>
                ))
              ) : (
                <div className="rail-empty">当前没有可导出的会话。</div>
              )}
            </div>
          </section>

          <section className="panel-surface admin-panel">
            <div className="section-head">
              <div>
                <span className="section-kicker">运行概览</span>
                <h2>账号与故障状态</h2>
              </div>
            </div>
            <div className="state-chip-grid">
              {Object.entries(dashboard.telemetry.account_state_counts).map(([state, count]) => (
                <div className="state-chip" key={state}>
                  <span>{formatAccountState(state)}</span>
                  <strong>{count}</strong>
                </div>
              ))}
            </div>
            <div className="state-chip-grid">
              <div className="state-chip">
                <span>BLOCKED</span>
                <strong>{dashboard.health.blocked}</strong>
              </div>
              <div className="state-chip">
                <span>COOLDOWN</span>
                <strong>{dashboard.health.cooling_down}</strong>
              </div>
              <div className="state-chip">
                <span>OFFLINE</span>
                <strong>{dashboard.health.unavailable}</strong>
              </div>
            </div>
          </section>

          <section className="panel-surface admin-panel">
            <div className="section-head">
              <div>
                <span className="section-kicker">近期文件</span>
                <h2>上传与媒体活动</h2>
              </div>
            </div>
            <div className="admin-session-list">
              {dashboard.assets.length ? (
                dashboard.assets.map((asset) => (
                  <div className="admin-session-row" key={asset.asset_id}>
                    <div>
                      <strong>{asset.filename}</strong>
                      <p>
                        {asset.owner_subject} / {asset.mime_type}
                      </p>
                    </div>
                    <div className="admin-session-row__meta">
                      <span className={`status-pill compact state-badge state-${asset.status}`}>{asset.status}</span>
                      <small>{formatTimestamp(asset.expires_at || asset.created_at)}</small>
                    </div>
                  </div>
                ))
              ) : (
                <div className="rail-empty">近期没有文件活动。</div>
              )}
            </div>
          </section>
        </aside>
      </section>
    </main>
  );
}
