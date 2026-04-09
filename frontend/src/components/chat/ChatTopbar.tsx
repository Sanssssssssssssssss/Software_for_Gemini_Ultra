import type { SessionDraft } from "../../chat/types";
import type { AccountSummary, UiMe } from "../../lib/api";
import { WorkspaceTopbar } from "../WorkspaceTopbar";

type ChatTopbarProps = {
  accounts: AccountSummary[];
  draft: SessionDraft;
  me: UiMe;
  onAllowFailoverChange: (value: boolean) => void;
  onLogout: () => void;
  onPinnedAccountChange: (value: string) => void;
};

export function ChatTopbar({
  accounts,
  draft,
  me,
  onAllowFailoverChange,
  onLogout,
  onPinnedAccountChange,
}: ChatTopbarProps) {
  return (
    <WorkspaceTopbar
      active="chat"
      className="chat-topbar"
      me={me}
      onLogout={onLogout}
      toolbar={
        me.is_admin ? (
          <div className="workspace-routebar">
            <label className="workspace-routebar__inline">
              <span className="workspace-routebar__label">路由</span>
              <select
                className="workspace-routebar__select"
                value={draft.pinnedAccountId}
                onChange={(event) => onPinnedAccountChange(event.target.value)}
              >
                <option value="">自动路由到可用账号</option>
                {accounts.map((account) => (
                  <option key={account.account_id} value={account.account_id}>
                    {account.account_id} · {account.state}
                  </option>
                ))}
              </select>
            </label>
            <label className="toggle-chip">
              <input
                checked={draft.allowFailover}
                onChange={(event) => onAllowFailoverChange(event.target.checked)}
                type="checkbox"
              />
              <span>故障切换</span>
            </label>
          </div>
        ) : null
      }
    />
  );
}
