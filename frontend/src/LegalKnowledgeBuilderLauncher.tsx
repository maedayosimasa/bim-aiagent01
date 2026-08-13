import { useEffect, useRef, useState } from "react";
import {
  getLegalStatus,
  startLegalKnowledgeBuilder,
  type LegalApiStatus,
} from "./api/client";

// 初回起動時はHuggingFaceからの埋め込みモデル(multilingual-e5-base)
// ダウンロード・ロードで数十秒〜数分かかることがあるため、起動ボタンを押した後は
// 一定時間ポーリングして自動で接続状態を反映する(手動で「再確認」を連打
// させない)。
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 3 * 60 * 1000;

function LegalKnowledgeBuilderLauncher() {
  const [status, setStatus] = useState<LegalApiStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [polling, setPolling] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshStatus = () => getLegalStatus().then(setStatus).catch(() => setStatus(null));

  useEffect(() => {
    refreshStatus();
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, []);

  const stopPolling = () => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    setPolling(false);
  };

  const pollUntilReachableOrTimeout = () => {
    stopPolling();
    setPolling(true);
    const deadline = Date.now() + POLL_TIMEOUT_MS;

    pollTimerRef.current = setInterval(() => {
      getLegalStatus()
        .then((s) => {
          setStatus(s);
          if (s.reachable) {
            stopPolling();
            setMessage("接続できました。");
          } else if (Date.now() > deadline) {
            stopPolling();
            setMessage(
              "まだ接続できていません。時間をおいて「状態を再確認」を押すか、" +
                "backendのログ(.legal_knowledge_builder_serve.log)を確認してください。"
            );
          }
        })
        .catch(() => {
          if (Date.now() > deadline) stopPolling();
        });
    }, POLL_INTERVAL_MS);
  };

  const handleStart = () => {
    setStarting(true);
    setMessage(null);
    setErrorMessage(null);

    startLegalKnowledgeBuilder()
      .then((result) => {
        if (result.already_running) {
          setMessage("既に起動中です。");
          refreshStatus();
        } else {
          setMessage(
            "起動処理を開始しました(PID " +
              result.pid +
              ")。初回は埋め込みモデルのダウンロードのため数十秒〜数分かかることがあります。"
          );
          pollUntilReachableOrTimeout();
        }
      })
      .catch((err) => setErrorMessage(String(err)))
      .finally(() => setStarting(false));
  };

  return (
    <div className="archicad-connection legal-launcher">
      <h3>Legal Knowledge Builder</h3>

      <p className="hint">
        法令検索・法規レポートが使う検索API(別リポジトリ「Legal Knowledge
        Builder」、backendと同じホスト上で動く別プロセス)をローカル起動します。
        本番のAWSデプロイではこの機能は使えません(手動で
        `uv run legal-knowledge-builder serve` を起動してください)。
      </p>

      <div className="archicad-connection-actions">
        <button onClick={handleStart} disabled={starting || polling}>
          {starting ? "起動処理中..." : polling ? "接続待機中..." : "ローカル起動"}
        </button>
        <button onClick={refreshStatus} disabled={starting}>
          状態を再確認
        </button>
      </div>

      {message && <div className="hint">{message}</div>}
      {errorMessage && <p className="error">{errorMessage}</p>}

      <div className="archicad-connection-info">
        {status?.reachable && (
          <div className="connection-notice success">
            接続完了 — Legal Knowledge Builder APIに接続しています。
          </div>
        )}

        {status && status.configured && !status.reachable && (
          <div className="connection-notice warning">
            まだ接続できていません({status.error ?? "不明なエラー"})。
          </div>
        )}

        {status && !status.configured && (
          <div className="connection-notice warning">
            LEGAL_API_URLが未設定です。「ローカル起動」を押すと自動で
            http://127.0.0.1:8100 に設定されます。
          </div>
        )}
      </div>
    </div>
  );
}

export default LegalKnowledgeBuilderLauncher;
