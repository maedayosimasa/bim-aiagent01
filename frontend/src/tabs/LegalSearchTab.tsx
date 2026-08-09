import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { searchLegal, getLegalStatus } from "../api/client";

function LegalSearchTab() {
  const [query, setQuery] = useState("");

  const statusQuery = useQuery({
    queryKey: ["legal-status"],
    queryFn: getLegalStatus,
  });

  const searchMutation = useMutation({
    mutationFn: (q: string) => searchLegal(q, 10),
  });

  const runSearch = () => {
    if (query.trim()) {
      searchMutation.mutate(query.trim());
    }
  };

  return (
    <div className="tab-panel">
      <h2>法令検索</h2>
      <p className="hint">
        Legal Knowledge Builder(建築基準法・建築士法・都市計画法等のKnowledge
        Package)を意味検索する。別プロセスで動く検索APIを参照しており、
        法令データの再ビルド・再起動は本プロジェクトとは独立して行われる。
      </p>

      {statusQuery.data && !statusQuery.data.reachable && (
        <p className="error">
          Legal APIに接続できません(
          {statusQuery.data.configured
            ? "LEGAL_API_URLは設定済みですが応答がありません"
            : "LEGAL_API_URL未設定"}
          )。`uv run legal-knowledge-builder serve` が起動しているか確認してください。
        </p>
      )}

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

      {searchMutation.data && (
        <>
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
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export default LegalSearchTab;
