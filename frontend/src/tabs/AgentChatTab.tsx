import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getAgentHistory,
  getAgentStatus,
  sendAgentMessage,
  type AgentHistoryMessage,
} from "../api/client";

// backend/src/backend/agent/(LangGraph)のsession_id = LangGraphのthread_id。
// タブを閉じても会話が続けられるようlocalStorageに保持する
// (会話履歴自体はバックエンド側のSQLite checkpointerに永続化されている)。
const SESSION_STORAGE_KEY = "bim-agent-session-id";

// 応答待ちがこの秒数を超えたら「応答に時間がかかっています」の案内を出す。
// backend側のタイムアウト(api/client.tsのAGENT_TIMEOUT_MS、3分)より
// 十分短く設定し、フリーズしたように見える前に状況を伝える。
const SLOW_RESPONSE_HINT_SECONDS = 30;

function loadOrCreateSessionId(): string {
  const existing = window.localStorage.getItem(SESSION_STORAGE_KEY);
  if (existing) return existing;
  const created = crypto.randomUUID();
  window.localStorage.setItem(SESSION_STORAGE_KEY, created);
  return created;
}

// activeがtrueの間、経過秒数を1秒おきに更新して返す(「考え中...」表示用)。
// 2026-08-11: backendが実際にはエラー応答を返しているのに、プロセス再起動
// 等でTCP接続が宙に浮くとブラウザのfetch()が無期限に待ち続け、「考え中...」
// のまま止まって見える事故があった。api/client.ts側にタイムアウトを追加した
// 上で、待ち時間を可視化してユーザーが異常に気づけるようにする。
function useElapsedSeconds(active: boolean): number {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (!active) {
      return;
    }

    const startedAt = Date.now();

    const interval = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);

    return () => {
      window.clearInterval(interval);
      setSeconds(0);
    };
  }, [active]);

  return seconds;
}

function AgentChatTab() {
  const queryClient = useQueryClient();
  const [sessionId, setSessionId] = useState(loadOrCreateSessionId);
  const [draft, setDraft] = useState("");
  // 送信直後、履歴の再取得が完了するまでの間だけユーザー発話を楽観的に表示する。
  const [pendingHuman, setPendingHuman] = useState<string | null>(null);
  const elapsedSeconds = useElapsedSeconds(pendingHuman !== null);

  const statusQuery = useQuery({ queryKey: ["agent-status"], queryFn: getAgentStatus });

  const historyQuery = useQuery({
    queryKey: ["agent-history", sessionId],
    queryFn: () => getAgentHistory(sessionId),
  });

  const chatMutation = useMutation({
    mutationFn: (message: string) => sendAgentMessage(sessionId, message),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agent-history", sessionId] });
      setPendingHuman(null);
    },
    onError: () => setPendingHuman(null),
  });

  const sendMessage = () => {
    const message = draft.trim();
    if (!message || pendingHuman !== null) return;
    setDraft("");
    setPendingHuman(message);
    chatMutation.mutate(message);
  };

  const startNewSession = () => {
    const created = crypto.randomUUID();
    window.localStorage.setItem(SESSION_STORAGE_KEY, created);
    setSessionId(created);
    setDraft("");
    setPendingHuman(null);
  };

  const configured = statusQuery.data?.configured ?? true; // 状態取得前は未知のためボタンを塞がない
  const inputDisabled = pendingHuman !== null || !configured;
  const messages: AgentHistoryMessage[] = historyQuery.data?.messages ?? [];

  return (
    <div className="tab-panel">
      <h2>AIエージェント</h2>

      <p className="hint">
        BIMデータの検索・空間解析・避難経路・法規チェック(参考値)・法令検索の
        ツールを自律的に呼び出して回答します。Archicad本体への書き込みは行いません
        (要素の移動・削除・プロパティ変更は他のタブから手動で行ってください)。
      </p>

      {statusQuery.data && !statusQuery.data.configured && (
        <p className="error">
          ANTHROPIC_API_KEYが未設定のため、エージェントは利用できません。
          バックエンドの環境変数に設定して再起動してください。
        </p>
      )}

      <div className="status-line">
        <span>
          セッション: <span className="mono">{sessionId}</span>
        </span>
        {statusQuery.data && <span>モデル: {statusQuery.data.model}</span>}
        <button onClick={startNewSession} disabled={pendingHuman !== null}>
          新しい会話を開始
        </button>
      </div>

      {historyQuery.isLoading && <p>読み込み中...</p>}
      {historyQuery.isError && <p className="error">{String(historyQuery.error)}</p>}

      <div className="agent-messages">
        {messages.length === 0 && pendingHuman === null && !historyQuery.isLoading && (
          <p className="hint">まだメッセージがありません。下から質問してください。</p>
        )}
        {messages.map((msg, index) => (
          <AgentMessageBubble key={index} message={msg} />
        ))}
        {pendingHuman !== null && (
          <>
            <div className="agent-message agent-message-human">{pendingHuman}</div>
            <div className="agent-message agent-message-ai agent-message-pending">
              考え中...({elapsedSeconds}秒)
              {elapsedSeconds >= SLOW_RESPONSE_HINT_SECONDS && (
                <div className="agent-slow-hint">
                  応答に時間がかかっています。BIMデータが多い場合や複数のツールを
                  呼び出している場合は数十秒かかることがあります(最大3分待っても
                  応答が無い場合は自動的にエラー表示されます)。
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {chatMutation.isError && <p className="error">{String(chatMutation.error)}</p>}

      <div className="agent-input-row">
        <textarea
          placeholder="例: 避難経路が届かない部屋を教えて"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              sendMessage();
            }
          }}
          disabled={inputDisabled}
          rows={3}
        />
        <button onClick={sendMessage} disabled={inputDisabled || !draft.trim()}>
          {pendingHuman !== null ? `送信中...(${elapsedSeconds}秒)` : "送信"}
        </button>
      </div>
    </div>
  );
}

function AgentMessageBubble({ message }: { message: AgentHistoryMessage }) {
  if (message.role === "human") {
    return <div className="agent-message agent-message-human">{message.content}</div>;
  }

  if (message.role === "tool") {
    return (
      <details className="agent-tool-call">
        <summary>ツール実行: {message.name}</summary>
        <pre className="json-block">{message.content}</pre>
      </details>
    );
  }

  return <div className="agent-message agent-message-ai">{message.content}</div>;
}

export default AgentChatTab;
