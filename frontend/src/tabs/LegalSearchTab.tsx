import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { searchLegal, type LegalSearchBackend } from "../api/client";
import LegalKnowledgeStatus from "./LegalKnowledgeStatus";
import SearchResultDetail from "./SearchResultDetail";

const BACKEND_LABELS: Record<LegalSearchBackend, string> = {
  chroma: "ChromaDB(既定、ファイルベース)",
  pgvector: "pgvector(PostgreSQL)",
};

function LegalSearchTab() {
  const [query, setQuery] = useState("");
  const [backend, setBackend] = useState<LegalSearchBackend>("chroma");
  const [searchedBackend, setSearchedBackend] = useState<LegalSearchBackend | null>(null);
  const [expandedNodeId, setExpandedNodeId] = useState<string | null>(null);

  const searchMutation = useMutation({
    mutationFn: (q: string) => searchLegal(q, 10, undefined, backend),
  });

  const runSearch = () => {
    if (query.trim()) {
      setSearchedBackend(backend);
      setExpandedNodeId(null);
      searchMutation.mutate(query.trim());
    }
  };

  const changeBackend = (next: LegalSearchBackend) => {
    setBackend(next);
    // 検索先を切り替えただけでは再検索しない仕様のため、古い検索先の結果が
    // 新しい検索先の結果であるかのように見えてしまう(実際にあった混乱)。
    // 切り替えた時点で表示中の結果をクリアし、「検索」を押すまで何も表示しない。
    searchMutation.reset();
    setSearchedBackend(null);
    setExpandedNodeId(null);
  };

  return (
    <div className="tab-panel">
      <h2>法令検索</h2>
      <p className="hint">
        Legal Knowledge Builder(建築基準法・建築士法・都市計画法等のKnowledge
        Package)を意味検索する。別プロセスで動く検索APIを参照しており、
        法令データの再ビルド・再起動は本プロジェクトとは独立して行われる。
      </p>

      <LegalKnowledgeStatus />

      <div className="legal-backend-select">
        <span>検索先:</span>
        <label>
          <input
            type="radio"
            checked={backend === "chroma"}
            onChange={() => changeBackend("chroma")}
          />
          ChromaDB(既定、ファイルベース)
        </label>
        <label>
          <input
            type="radio"
            checked={backend === "pgvector"}
            onChange={() => changeBackend("pgvector")}
          />
          pgvector(PostgreSQL、要接続)
        </label>
      </div>

      <div className="button-row">
        <input
          type="text"
          placeholder="例: 敷地が道路に接する長さの基準は?"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && runSearch()}
        />
        <button onClick={runSearch} disabled={searchMutation.isPending}>
          {searchMutation.isPending ? "検索中..." : "検索"}
        </button>
      </div>

      {searchMutation.isError && (
        <p className="error">{String(searchMutation.error)}</p>
      )}

      {searchMutation.data && searchedBackend && (
        <>
          <p className="hint">
            検索先: <strong>{BACKEND_LABELS[searchedBackend]}</strong>({searchMutation.data.results.length}件)
          </p>
          {searchMutation.data.results.length === 0 && (
            <p>一致する条文がありません。</p>
          )}
          <ul className="search-results">
            {searchMutation.data.results.map((hit) => (
              <li key={hit.node_id}>
                <div className="search-result-header">
                  <strong>{hit.citation ?? hit.node_id}</strong>
                  <span className="distance">距離: {hit.distance.toFixed(3)}</span>
                </div>
                <div className="search-result-doc">{hit.text?.trim()}</div>
                {hit.law_id && (
                  <button
                    className="legal-detail-toggle"
                    onClick={() =>
                      setExpandedNodeId(expandedNodeId === hit.node_id ? null : hit.node_id)
                    }
                  >
                    {expandedNodeId === hit.node_id
                      ? "▲ ルール・引用関係を閉じる"
                      : "▼ ルール・引用関係を見る"}
                  </button>
                )}
                {expandedNodeId === hit.node_id && hit.law_id && (
                  <SearchResultDetail lawId={hit.law_id} nodeId={hit.node_id} />
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export default LegalSearchTab;
