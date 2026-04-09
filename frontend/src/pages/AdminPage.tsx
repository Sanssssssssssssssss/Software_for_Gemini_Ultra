import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { WorkspaceTopbar } from "../components/WorkspaceTopbar";
import {
  type AccountSummary,
  type AdminDashboard,
  type AdminManagedAccount,
  type AdminReauthJob,
  ApiError,
  buildAdminSessionExportUrl,
  buildAdminSessionsExportUrl,
  buildUiAssetContentUrl,
  cancelAdminReauth,
  completeAdminReauth,
  deleteAdminAccount,
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
  { key: "refresh", label: "刷新" },
  { key: "clear-cooldown", label: "清冷却" },
  { key: "mark-reauth-required", label: "标重登" },
  { key: "disable-runtime", label: "禁用" },
  { key: "enable-runtime", label: "启用" },
] as const;

function formatTimestamp(value: string | null | undefined) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function formatState(state: string) {
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
  if (!status) return "UNKNOWN";
  return status.toUpperCase();
}

function toFormState(account?: AdminManagedAccount | null): AccountFormState {
  if (!account) return { ...DEFAULT_ACCOUNT_FORM };
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
    const next = await getAdminDashboard();
    setDashboard(next);
    if (selectedAccountId) {
      const selected = next.inventory_accounts.find((item) => item.account_id === selectedAccountId);
      setAccountForm(toFormState(selected ?? null));
      if (!selected) setSelectedAccountId(null);
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const currentMe = await getMe();
        if (!currentMe.authenticated) return void navigate("/ui/login", { replace: true });
        if (!currentMe.is_admin) return void navigate("/ui/chat", { replace: true });
        const nextDashboard = await getAdminDashboard();
        if (!cancelled) {
          setMe(currentMe);
          setDashboard(nextDashboard);
        }
      } catch (caught) {
        if (!cancelled) {
          if (caught instanceof ApiError && caught.status === 401) {
            navigate("/ui/login", { replace: true });
            return;
          }
          setError(caught instanceof Error ? caught.message : "加载管理控制台失败。");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    const interval = window.setInterval(() => {
      void refreshDashboard().catch(() => undefined);
    }, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [navigate, selectedAccountId]);

  const selectedAccount = useMemo(
    () => dashboard?.inventory_accounts.find((item) => item.account_id === selectedAccountId) ?? null,
    [dashboard, selectedAccountId],
  );

  async function handleLogout() {
    await logout();
    navigate("/ui/login", { replace: true });
  }

  async function runAction(account: AccountSummary, action: string) {
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

  async function handleDeleteAccount(accountId: string) {
    if (!window.confirm(`确定删除账号 ${accountId} 吗？`)) return;
    const key = `${accountId}:delete`;
    setPendingActions((current) => ({ ...current, [key]: true }));
    setError("");
    setNotice("");
    try {
      await deleteAdminAccount(accountId);
      if (selectedAccountId === accountId) {
        setSelectedAccountId(null);
        setAccountForm({ ...DEFAULT_ACCOUNT_FORM });
      }
      setNotice(`账号 ${accountId} 已删除。`);
      await refreshDashboard();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除账号失败。");
    } finally {
      setPendingActions((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
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
      setError(caught instanceof Error ? caught.message : "取消任务失败。");
    } finally {
      setPendingActions((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  }

  async function saveAccount() {
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
        tags: accountForm.tags.split(",").map((item) => item.trim()).filter(Boolean),
      });
      setSelectedAccountId(saved.account_id);
      setAccountForm(toFormState(saved));
      setNotice(`账号 ${saved.account_id} 已保存。`);
      await refreshDashboard();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存账号失败。");
    }
  }

  if (!me && loading) {
    return <main className="page-shell page-shell--dashboard"><WorkspaceTopbar active="admin" /></main>;
  }
  if (!me || !dashboard) {
    return null;
  }

  return (
    <main className="page-shell page-shell--dashboard admin-shell">
      <WorkspaceTopbar active="admin" me={me} onLogout={() => void handleLogout()} />

      <section className="page-section page-section--compact">
        <div className="page-section__copy">
          <span className="section-kicker">管理台</span>
          <h1>账号恢复与系统控制台</h1>
          <p>账号、重登、会话导出和近期文件都在这里。</p>
        </div>
        <div className="page-section__actions">
          <span className="status-pill ok">LIVE</span>
          <button className="secondary-link compact-link button-reset" type="button" onClick={() => void refreshDashboard()}>
            刷新概览
          </button>
        </div>
      </section>

      <section className="admin-summary-strip">
        <article className="metric-card"><span>可路由</span><strong>{dashboard.health.ready_accounts}</strong></article>
        <article className="metric-card danger"><span>需重登</span><strong>{dashboard.health.reauth_required}</strong></article>
        <article className="metric-card"><span>请求中</span><strong>{dashboard.telemetry.active_requests}</strong></article>
        <article className="metric-card"><span>会话</span><strong>{dashboard.telemetry.chat_sessions}</strong></article>
        <article className="metric-card"><span>文件</span><strong>{dashboard.telemetry.chat_assets}</strong></article>
      </section>

      {error ? <div className="inline-error">{error}</div> : null}
      {notice ? <div className="inline-banner tone-success">{notice}</div> : null}

      <section className="admin-grid">
        <div className="admin-main">
          <section className="panel-surface admin-panel">
            <div className="section-head">
              <div><span className="section-kicker">账号库存</span><h2>编辑 / 重登 / 删除</h2></div>
              <button className="secondary-link compact-link button-reset" type="button" onClick={() => { setSelectedAccountId(null); setAccountForm({ ...DEFAULT_ACCOUNT_FORM }); }}>
                新建账号
              </button>
            </div>

            <div className="admin-control-grid">
              <div className="runtime-account-list">
                {dashboard.inventory_accounts.map((account) => {
                  const deleteKey = `${account.account_id}:delete`;
                  return (
                    <article className="runtime-account" key={account.account_id}>
                      <div className="runtime-account__head">
                        <div className="runtime-account__identity">
                          <strong>{account.account_id}</strong>
                          <p>{account.cookie_source_profile_dir || "未绑定浏览器 profile"}</p>
                        </div>
                        <span className={`status-pill compact state-badge state-${account.runtime?.state || "disabled"}`}>
                          {formatState(account.runtime?.state || "disabled")}
                        </span>
                      </div>
                      <div className="runtime-account__stats">
                        <div><span>Gemini</span><strong>{formatRuntimeStatus(account.runtime?.account_status ?? null)}</strong></div>
                        <div><span>并发</span><strong>{account.runtime?.active_requests ?? 0}/{account.max_concurrency}</strong></div>
                        <div><span>队列</span><strong>{account.runtime?.queue_depth ?? 0}</strong></div>
                        <div><span>Cookie</span><strong>{account.has_cookie_bundle ? "READY" : "MISSING"}</strong></div>
                      </div>
                      <div className="runtime-account__meta"><span>{account.runtime?.state_reason || "暂无状态补充"}</span><span>{account.tags.length ? account.tags.join(", ") : "无标签"}</span></div>
                      <div className="account-admin-card__actions">
                        <button className="secondary-link compact-link button-reset" type="button" onClick={() => { setSelectedAccountId(account.account_id); setAccountForm(toFormState(account)); }}>编辑</button>
                        <button className="secondary-link compact-link button-reset" type="button" onClick={() => void startAdminReauth(account.account_id).then(() => refreshDashboard())}>重登</button>
                        <button className="secondary-link compact-link button-reset tone-danger" data-testid={`admin-delete-account-${account.account_id}`} disabled={!!pendingActions[deleteKey]} type="button" onClick={() => void handleDeleteAccount(account.account_id)}>
                          {pendingActions[deleteKey] ? "删除中..." : "删除"}
                        </button>
                        {account.runtime ? ADMIN_ACTIONS.map((action) => {
                          const key = `${account.account_id}:${action.key}`;
                          return (
                            <button className="secondary-link compact-link button-reset" data-testid={`admin-action-${account.account_id}-${action.key}`} disabled={!!pendingActions[key]} key={action.key} type="button" onClick={() => void runAction(account.runtime as AccountSummary, action.key)}>
                              {pendingActions[key] ? "处理中..." : action.label}
                            </button>
                          );
                        }) : null}
                      </div>
                    </article>
                  );
                })}
              </div>

              <section className="panel-surface admin-side-card">
                <div className="section-head"><div><span className="section-kicker">{selectedAccount ? "编辑账号" : "新建账号"}</span><h3>{selectedAccount?.account_id || "账号资料"}</h3></div></div>
                <div className="admin-form-grid">
                  <label className="field"><span>账号 ID</span><input value={accountForm.account_id} onChange={(event) => setAccountForm((current) => ({ ...current, account_id: event.target.value }))} /></label>
                  <label className="field"><span>浏览器</span><select value={accountForm.cookie_source_browser} onChange={(event) => setAccountForm((current) => ({ ...current, cookie_source_browser: event.target.value }))}><option value="chrome">Chrome</option><option value="edge">Edge</option></select></label>
                  <label className="field admin-form-grid__full"><span>profile 目录</span><input value={accountForm.cookie_source_profile_dir} onChange={(event) => setAccountForm((current) => ({ ...current, cookie_source_profile_dir: event.target.value }))} /></label>
                  <label className="field admin-form-grid__full"><span>浏览器路径</span><input value={accountForm.cookie_source_browser_path} onChange={(event) => setAccountForm((current) => ({ ...current, cookie_source_browser_path: event.target.value }))} /></label>
                  <label className="field"><span>最大并发</span><input min={1} type="number" value={accountForm.max_concurrency} onChange={(event) => setAccountForm((current) => ({ ...current, max_concurrency: Number(event.target.value) || 1 }))} /></label>
                  <label className="field"><span>冷却秒数</span><input min={5} type="number" value={accountForm.cooldown_seconds} onChange={(event) => setAccountForm((current) => ({ ...current, cooldown_seconds: Number(event.target.value) || 60 }))} /></label>
                  <label className="field"><span>请求超时</span><input min={10} type="number" value={accountForm.request_timeout_seconds} onChange={(event) => setAccountForm((current) => ({ ...current, request_timeout_seconds: Number(event.target.value) || 450 }))} /></label>
                  <label className="field"><span>代理</span><input value={accountForm.proxy} onChange={(event) => setAccountForm((current) => ({ ...current, proxy: event.target.value }))} /></label>
                  <label className="field admin-form-grid__full"><span>标签</span><input value={accountForm.tags} onChange={(event) => setAccountForm((current) => ({ ...current, tags: event.target.value }))} /></label>
                </div>
                <div className="admin-toggle-row">
                  <label className="toggle-chip"><input checked={accountForm.enabled} type="checkbox" onChange={(event) => setAccountForm((current) => ({ ...current, enabled: event.target.checked }))} />启用账号</label>
                  <label className="toggle-chip"><input checked={accountForm.verify_ssl} type="checkbox" onChange={(event) => setAccountForm((current) => ({ ...current, verify_ssl: event.target.checked }))} />校验 SSL</label>
                </div>
                <div className="admin-form-actions"><button className="primary-button button-reset" type="button" onClick={() => void saveAccount()}>保存账号</button></div>
              </section>
            </div>
          </section>

          <section className="panel-surface admin-panel">
            <div className="section-head"><div><span className="section-kicker">重登任务</span><h2>同步列表</h2></div></div>
            <div className="admin-job-list">
              {dashboard.reauth_jobs.length ? dashboard.reauth_jobs.map((job) => {
                const completeKey = `job:${job.job_id}:complete`;
                const cancelKey = `job:${job.job_id}:cancel`;
                return (
                  <article className="admin-job-card" key={job.job_id}>
                    <div className="runtime-account__head">
                      <div className="runtime-account__identity"><strong>{job.account_id}</strong><p>{job.detail}</p></div>
                      <span className={`status-pill compact state-badge state-${job.status}`}>{job.status.toUpperCase()}</span>
                    </div>
                    <div className="runtime-account__meta"><span>{job.browser || "未知浏览器"}</span><span>{job.profile_dir || "未配置 profile"}</span></div>
                    <div className="account-admin-card__actions">
                      <button className="primary-button button-reset" disabled={!!pendingActions[completeKey] || job.status === "completed" || job.status === "cancelled"} type="button" onClick={() => void handleCompleteReauth(job)}>{pendingActions[completeKey] ? "同步中..." : "完成同步"}</button>
                      <button className="secondary-link compact-link button-reset" disabled={!!pendingActions[cancelKey] || !job.launched} type="button" onClick={() => void handleCancelReauth(job)}>{pendingActions[cancelKey] ? "取消中..." : "取消任务"}</button>
                    </div>
                  </article>
                );
              }) : <div className="rail-empty">目前没有重登任务。</div>}
            </div>
          </section>
        </div>

        <aside className="admin-side">
          <section className="panel-surface admin-panel">
            <div className="section-head">
              <div><span className="section-kicker">会话导出</span><h2>最近会话</h2></div>
              <div className="app-nav">
                <button className="secondary-link compact-link button-reset" type="button" onClick={() => openDownload(buildAdminSessionsExportUrl({ format: "json", limit: 200 }))}>全部 JSON</button>
                <button className="secondary-link compact-link button-reset" type="button" onClick={() => openDownload(buildAdminSessionsExportUrl({ format: "markdown", limit: 200 }))}>全部 Markdown</button>
              </div>
            </div>
            <div className="admin-session-list">
              {dashboard.sessions.length ? dashboard.sessions.map((session, index) => (
                <div className="admin-session-row" key={session.session_id}>
                  <div><strong>{session.title || `会话 ${String(index + 1).padStart(2, "0")}`}</strong><p>{session.account_id} / {session.routing_policy}</p></div>
                  <div className="admin-session-row__meta">
                    <span className={`status-pill compact state-badge state-${session.status}`}>{formatState(session.status)}</span>
                    <small>{formatTimestamp(session.updated_at || session.created_at)}</small>
                    <div className="app-nav">
                      <button className="secondary-link compact-link button-reset" type="button" onClick={() => openDownload(buildAdminSessionExportUrl(session.session_id, "json"))}>JSON</button>
                      <button className="secondary-link compact-link button-reset" type="button" onClick={() => openDownload(buildAdminSessionExportUrl(session.session_id, "markdown"))}>Markdown</button>
                    </div>
                  </div>
                </div>
              )) : <div className="rail-empty">当前没有可导出的会话。</div>}
            </div>
          </section>

          <section className="panel-surface admin-panel">
            <div className="section-head"><div><span className="section-kicker">运行概览</span><h2>状态分布</h2></div></div>
            <div className="state-chip-grid">
              {Object.entries(dashboard.telemetry.account_state_counts).map(([state, count]) => (
                <div className="state-chip" key={state}><span>{formatState(state)}</span><strong>{count}</strong></div>
              ))}
            </div>
          </section>

          <section className="panel-surface admin-panel">
            <div className="section-head"><div><span className="section-kicker">近期文件</span><h2>打开 / 下载</h2></div></div>
            <div className="admin-session-list admin-asset-list">
              {dashboard.assets.length ? dashboard.assets.map((asset) => {
                const href = buildUiAssetContentUrl(asset.asset_id);
                const isImage = asset.mime_type.startsWith("image/");
                return (
                  <div className="admin-session-row admin-asset-row" key={asset.asset_id}>
                    <div className="admin-asset-row__main">
                      {isImage ? <img alt={asset.filename} className="admin-asset-thumb" src={href} /> : null}
                      <div><strong>{asset.filename}</strong><p>{asset.owner_subject} / {asset.mime_type}</p></div>
                    </div>
                    <div className="admin-session-row__meta">
                      <span className={`status-pill compact state-badge state-${asset.status}`}>{asset.status.toUpperCase()}</span>
                      <small>{formatTimestamp(asset.expires_at || asset.created_at)}</small>
                      <div className="app-nav">
                        <a className="secondary-link compact-link" href={href} rel="noreferrer" target="_blank">打开</a>
                        <a className="secondary-link compact-link" download={asset.filename} href={href}>下载</a>
                      </div>
                    </div>
                  </div>
                );
              }) : <div className="rail-empty">近期没有文件活动。</div>}
            </div>
          </section>
        </aside>
      </section>
    </main>
  );
}
