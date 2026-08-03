import { useEffect, useState } from "react";
import {
  getArchicadConnection,
  getArchicadStatus,
  setArchicadConnection,
  type ArchicadConnectionInfo,
  type ArchicadStatus,
} from "./api/client";

type Mode = "local" | "wsl" | "remote" | "custom";

// WSL2上のbackendから、同じ物理PC上のWindows側archicad-mcpブリッジ(127.0.0.1:8765)へ
// 到達するための既定ゲートウェイIP。WSLとWindowsは別ネットワーク名前空間のため、
// 127.0.0.1では届かずこのIPが必要(2026-08-04に実機で確認・接続成功済み)。
// WSL再起動でゲートウェイIPが変わる可能性があるので、繋がらなければ
// `ip route`(WSL側)で現在値を確認して「カスタムURL」で入力し直すこと。
const WSL_BRIDGE_URL = "http://172.18.16.1:8765/mcp/";

function ArchicadConnection() {
  const [info, setInfo] = useState<ArchicadConnectionInfo | null>(null);
  const [status, setStatus] = useState<ArchicadStatus | null>(null);
  const [mode, setMode] = useState<Mode>("wsl");
  const [customUrl, setCustomUrl] = useState("");
  const [loading, setLoading] = useState(false);

  const fetchStatus = () => {
    getArchicadStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  };

  const applyConnection = (urlOverride?: string | null) => {
    setLoading(true);

    const url =
      urlOverride !== undefined
        ? urlOverride
        : mode === "local"
        ? "local"
        : mode === "wsl"
        ? WSL_BRIDGE_URL
        : mode === "remote"
        ? null
        : customUrl;

    return setArchicadConnection(url)
      .then((data) => {
        setInfo(data.connection);
        setStatus(data.status);
        return data.status;
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    getArchicadConnection()
      .then((fetchedInfo) => {
        // まだ何も明示的に選択されていなければ、まずWSLブリッジ接続を試し、
        // 繋がればそれを既定にする。繋がらなければローカルにフォールバックする。
        // (このデフォルトはbackend再起動ごとにリセットされるランタイム
        // オーバーライドなので、フロント側で起動時に一度合わせる。)
        if (!fetchedInfo.overridden) {
          setMode("wsl");
          applyConnection(WSL_BRIDGE_URL).then((appliedStatus) => {
            if (!appliedStatus?.reachable) {
              setMode("local");
              applyConnection("local");
            }
          });
          return;
        }

        setInfo(fetchedInfo);

        if (fetchedInfo.active_url === WSL_BRIDGE_URL) {
          setMode("wsl");
        } else if (fetchedInfo.active_url === fetchedInfo.local_preset_url) {
          setMode("local");
        } else if (fetchedInfo.active_url === fetchedInfo.env_default) {
          setMode("remote");
        } else if (fetchedInfo.active_url) {
          setMode("custom");
          setCustomUrl(fetchedInfo.active_url);
        }

        fetchStatus();
      })
      .catch(() => setInfo(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="archicad-connection">
      <h3>Archicad接続</h3>

      <div className="archicad-connection-modes">
        <label>
          <input
            type="radio"
            checked={mode === "local"}
            onChange={() => setMode("local")}
          />
          ローカル({info?.local_preset_url ?? "127.0.0.1:8765"})
        </label>
        <label>
          <input
            type="radio"
            checked={mode === "wsl"}
            onChange={() => setMode("wsl")}
          />
          WSLブリッジ({WSL_BRIDGE_URL})
        </label>
        <label>
          <input
            type="radio"
            checked={mode === "remote"}
            onChange={() => setMode("remote")}
          />
          リモート(Tailscale、env既定: {info?.env_default ?? "未設定"})
        </label>
        <label>
          <input
            type="radio"
            checked={mode === "custom"}
            onChange={() => setMode("custom")}
          />
          カスタムURL
        </label>
      </div>

      {mode === "custom" && (
        <input
          type="text"
          className="archicad-connection-custom-url"
          placeholder="https://example.ts.net/mcp/"
          value={customUrl}
          onChange={(e) => setCustomUrl(e.target.value)}
        />
      )}

      <div className="archicad-connection-actions">
        <button onClick={() => applyConnection()} disabled={loading}>
          {loading ? "切替中..." : "この接続に切り替える"}
        </button>
        <button onClick={fetchStatus}>状態を再確認</button>
      </div>

      <div className="archicad-connection-info">
        <div>現在の接続先: {info?.active_url ?? "(未設定)"}</div>

        {status?.reachable && (
          <div className="connection-notice success">
            接続完了 - Archicad MCPブリッジに接続しました(ツール
            {status.tools?.length ?? 0}件)。
          </div>
        )}

        {status && status.configured && !status.reachable && (
          <div className="connection-notice warning">
            <div>
              Archicad MCPブリッジ({info?.active_url})に接続できていません
              ({status.error ?? "不明なエラー"})。
            </div>
            <div>
              ローカル開発時は、Windows PC側で以下を実行してブリッジを
              起動してください(本番のAWSデプロイでは、このスクリプトは
              Windows PC側でタスクスケジューラにより自動起動される想定です)。
            </div>
            <code>C:\Users\gdre1\archicad-mcp\start_http.ps1</code>
          </div>
        )}

        {status && !status.configured && (
          <div className="connection-notice warning">未設定</div>
        )}
      </div>
    </div>
  );
}

export default ArchicadConnection;
