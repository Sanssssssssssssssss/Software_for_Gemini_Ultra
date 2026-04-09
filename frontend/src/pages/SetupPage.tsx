import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { WorkspaceTopbar } from "../components/WorkspaceTopbar";
import { type SetupStatus, type UiMe, getMe, getSetupStatus } from "../lib/api";

const DEFAULT_STEPS = [
  "运行 `py scripts/bootstrap_local.py` 生成本地配置。",
  "在 `config/accounts.json` 中填入可用的 Gemini Web Cookie。",
  "执行 `py scripts/doctor.py` 检查本机环境。",
  "使用 `python scripts/run_local.py --env-file .env` 启动服务。",
];

const CHECK_NAME_MAP: Record<string, string> = {
  env_file: "环境变量文件",
  api_auth: "API 鉴权",
  ui_password: "前端密码",
  ui_session_secret: "前端会话密钥",
  accounts_path: "账号配置路径",
  accounts_permissions: "账号目录权限",
  accounts_credentials: "账号 Cookie",
  cookie_autosync: "Cookie 自动同步",
  frontend_dist: "前端构建产物",
  asset_root: "文件存储目录",
  database_path: "数据库目录",
  runtime_readiness: "运行时就绪情况",
};

function translateCheckName(name: string) {
  return CHECK_NAME_MAP[name] || name;
}

function translateDetail(detail: string) {
  return detail
    .replace("Environment file detected at", "已检测到环境变量文件：")
    .replace("Configured 1 API bearer token(s).", "已配置 1 个 API Bearer Token。")
    .replace("The UI password is no longer using the default placeholder.", "前端密码已脱离默认占位值。")
    .replace("The UI session signing secret is no longer using the default placeholder.", "前端会话签名密钥已脱离默认占位值。")
    .replace("Account inventory directory is ready:", "账号目录已就绪：")
    .replace("Detected 1 configured account(s) with non-placeholder cookies.", "已检测到 1 个配置完成且 Cookie 非占位值的账号。")
    .replace(
      "Cookie autosync is enabled, but no account has a persistent browser profile configured.",
      "Cookie 自动同步已开启，但还没有账号配置持久浏览器目录。",
    )
    .replace(
      "Set cookie_source_profile_dir for an account or disable cookie autosync.",
      "为某个账号设置 cookie_source_profile_dir，或关闭 cookie_autosync。",
    )
    .replace("React frontend build detected at", "已检测到前端构建产物：")
    .replace("Asset storage directory is ready at", "文件存储目录已就绪：")
    .replace("SQLite database directory is ready at", "SQLite 数据库目录已就绪：")
    .replace("1/1 account(s) are currently ready and satisfy startup requirements.", "当前 1/1 个账号满足启动要求。");
}

function translateStep(step: string) {
  return step.replace(
    "Set cookie_source_profile_dir for an account or disable cookie autosync.",
    "为某个账号设置 cookie_source_profile_dir，或关闭 cookie_autosync。",
  );
}

function translateCheckStatus(status: "pass" | "warn" | "fail") {
  if (status === "pass") {
    return "通过";
  }
  if (status === "warn") {
    return "警告";
  }
  return "失败";
}

export function SetupPage() {
  const [me, setMe] = useState<UiMe | null>(null);
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    void Promise.all([getSetupStatus(), getMe().catch(() => null)])
      .then(([payload, currentMe]) => {
        if (!cancelled) {
          setStatus(payload);
          setMe(currentMe);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "加载配置状态失败。");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const nextSteps = useMemo(
    () => (status?.next_steps?.length ? status.next_steps : DEFAULT_STEPS),
    [status],
  );

  const summary = useMemo(() => {
    const checks = status?.checks ?? [];
    return {
      pass: checks.filter((item) => item.status === "pass").length,
      warn: checks.filter((item) => item.status === "warn").length,
      fail: checks.filter((item) => item.status === "fail").length,
    };
  }, [status]);

  return (
    <main className="page-shell page-shell--dashboard">
      <WorkspaceTopbar active="setup" me={me} />

      <section className="page-section page-section--compact">
        <div className="page-section__copy">
          <span className="section-kicker">配置面板</span>
          <h1>服务准备情况</h1>
          <p>先把阻塞项清掉，再进入登录、聊天和管理。这里应该像状态面板，而不是一整屏说明书。</p>
        </div>
        <div className="page-section__actions">
          <span className={`status-pill ${status?.setup_complete ? "ok" : "warn"}`}>
            {status?.setup_complete ? "READY" : "CHECKS"}
          </span>
          <Link className="secondary-link compact-link" to="/ui/login">
            前往登录
          </Link>
        </div>
      </section>

      <section className="setup-summary-strip">
        <article className="metric-card">
          <span>通过项</span>
          <strong>{summary.pass}</strong>
          <p>已经满足的前置条件。</p>
        </article>
        <article className="metric-card">
          <span>警告项</span>
          <strong>{summary.warn}</strong>
          <p>不一定阻塞，但建议尽快处理。</p>
        </article>
        <article className="metric-card danger">
          <span>阻塞项</span>
          <strong>{summary.fail}</strong>
          <p>这些问题会直接影响服务可用性。</p>
        </article>
      </section>

      <section className="setup-grid">
        <div className="panel-surface setup-panel">
          <div className="section-head">
            <div>
              <span className="section-kicker">检查项</span>
              <h2>当前状态</h2>
            </div>
            {loading ? <span className="body-muted">刷新中...</span> : null}
          </div>

          {error ? <div className="inline-error">{error}</div> : null}

          <div className="setup-check-grid">
            {status?.checks?.map((check) => (
              <article key={check.name} className={`check-card check-${check.status}`}>
                <div className="check-header">
                  <span
                    className={`status-pill compact ${
                      check.status === "pass" ? "ok" : check.status === "warn" ? "warn" : "danger"
                    }`}
                  >
                    {translateCheckStatus(check.status)}
                  </span>
                  <strong>{translateCheckName(check.name)}</strong>
                </div>
                <p>{translateDetail(check.detail)}</p>
                {check.action ? <div className="body-muted">建议动作：{translateDetail(check.action)}</div> : null}
              </article>
            ))}
          </div>
        </div>

        <aside className="setup-side">
          <section className="panel-surface setup-panel">
            <div className="section-head">
              <div>
                <span className="section-kicker">推荐流程</span>
                <h2>按顺序处理</h2>
              </div>
            </div>

            <div className="stack-md">
              {nextSteps.map((step, index) => (
                <div key={`${index}-${step}`} className="step-card">
                  <span className="step-index">{index + 1}</span>
                  <p>{translateStep(step)}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="panel-surface setup-panel">
            <div className="section-head">
              <div>
                <span className="section-kicker">运行提醒</span>
                <h2>别把可启动当成可稳定运行</h2>
              </div>
            </div>

            <div className="setup-note-list">
              <div className="terminal-note">
                <span className="terminal-note__prompt">$</span>
                <div>
                  <strong>Cookie 依赖</strong>
                  <p>就算配置检查通过，账号 Cookie 过期或上游变动仍然会影响运行表现。</p>
                </div>
              </div>
              <div className="terminal-note">
                <span className="terminal-note__prompt">$</span>
                <div>
                  <strong>运行时健康</strong>
                  <p>配置完成只代表可以启动，不代表账号池在高并发或长时间运行时一定稳定。</p>
                </div>
              </div>
            </div>
          </section>
        </aside>
      </section>
    </main>
  );
}
