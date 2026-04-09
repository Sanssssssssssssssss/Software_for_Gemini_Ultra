import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { WorkspaceTopbar } from "../components/WorkspaceTopbar";
import { getMe, getSetupStatus, login } from "../lib/api";

type FormState = {
  username: string;
  password: string;
};

export function LoginPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState<FormState>({ username: "", password: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [setupReady, setSetupReady] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;

    void Promise.all([getMe(), getSetupStatus()])
      .then(([me, setup]) => {
        if (cancelled) {
          return;
        }
        setSetupReady(setup.setup_complete);
        if (me.authenticated) {
          navigate("/ui/chat", { replace: true });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSetupReady(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [navigate]);

  const helperText = useMemo(() => {
    if (setupReady === null) {
      return "正在检查服务状态与前端访问权限。";
    }
    if (setupReady) {
      return "服务已就绪，输入工作区账号后即可进入聊天和管理台。";
    }
    return "当前还有阻塞项，建议先查看配置页，确认服务和账号池状态。";
  }, [setupReady]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await login(form.username, form.password);
      navigate("/ui/chat", { replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "登录失败。");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="page-shell page-shell--dashboard login-shell">
      <WorkspaceTopbar active="login" />

      <section className="page-section page-section--compact">
        <div className="page-section__copy">
          <span className="section-kicker">工作区入口</span>
          <h1>Gemini 内部工作台</h1>
          <p>统一接入聊天、上传、账号池和管理操作，导航保持固定，真正工作的部分留给下面的主面板。</p>
        </div>
        <div className="page-section__actions">
          <span className={`status-pill ${setupReady ? "ok" : "warn"}`}>{setupReady ? "READY" : "SETUP"}</span>
        </div>
      </section>

      <section className="auth-layout">
        <div className="panel-surface auth-card">
          <div className="stack-sm">
            <span className="section-kicker">登录</span>
            <h2>进入工作区</h2>
            <p className="body-muted">{helperText}</p>
          </div>

          <form className="stack-lg" onSubmit={onSubmit}>
            <label className="field">
              <span>用户名</span>
              <input
                autoComplete="username"
                data-testid="login-username"
                onChange={(event) => setForm((current) => ({ ...current, username: event.target.value }))}
                placeholder="admin"
                required
                value={form.username}
              />
            </label>

            <label className="field">
              <span>密码</span>
              <input
                autoComplete="current-password"
                data-testid="login-password"
                onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))}
                placeholder="输入登录密码"
                required
                type="password"
                value={form.password}
              />
            </label>

            {error ? <div className="inline-error">{error}</div> : null}

            <div className="auth-card__actions">
              <button className="primary-button" data-testid="login-submit" disabled={loading} type="submit">
                {loading ? "登录中..." : "进入工作区"}
              </button>
              <Link className="secondary-link" to="/setup">
                查看配置
              </Link>
            </div>
          </form>
        </div>

        <aside className="panel-surface status-panel">
          <div className="stack-sm">
            <span className="section-kicker">入口说明</span>
            <h2>这页只负责进入，不抢主任务</h2>
          </div>

          <div className="status-grid">
            <article className="status-card">
              <strong>管理员</strong>
              <p>登录后可直接查看账号池状态、执行恢复动作，并处理异常账号。</p>
            </article>
            <article className="status-card">
              <strong>普通用户</strong>
              <p>只会看到自己的会话，聊天、附件上传和历史浏览都在同一个界面内完成。</p>
            </article>
            <article className="status-card">
              <strong>文件能力</strong>
              <p>图片、PDF、PPTX 都能直接进入聊天流程，不需要切到额外的工具页。</p>
            </article>
          </div>

          <div className="panel-divider" />

          <div className="terminal-note">
            <span className="terminal-note__prompt">$</span>
            <div>
              <strong>进入前建议</strong>
              <p>如果这里显示 SETUP，先去配置页确认账号池、健康检查和服务启动状态。</p>
            </div>
          </div>
        </aside>
      </section>
    </main>
  );
}
