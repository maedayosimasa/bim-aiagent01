import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { searchElements } from "../api/client";

function SearchTab() {
  const [query, setQuery] = useState("");

  const searchMutation = useMutation({
    mutationFn: (q: string) => searchElements(q, 10),
  });

  const runSearch = () => {
    if (query.trim()) {
      searchMutation.mutate(query.trim());
    }
  };

  return (
    <div className="tab-panel">
      <h2>意味検索</h2>

      <div className="button-row">
        <input
          type="text"
          placeholder="例: 隣接する居室のドア"
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
            <p>
              一致する要素がありません。先に「要素同期」タブで同期・インデックス化
              を行ってください。
            </p>
          )}
          <ul className="search-results">
            {searchMutation.data.results.map((hit) => (
              <li key={hit.guid}>
                <div className="search-result-header">
                  <strong>{hit.name ?? "(無名)"}</strong>
                  <span className="tag">{hit.type}</span>
                  <span className="distance">距離: {hit.distance.toFixed(3)}</span>
                </div>
                <div className="search-result-doc">{hit.document}</div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export default SearchTab;
