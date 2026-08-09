import { useQuery } from "@tanstack/react-query";
import {
  getLegalReference,
  getLegalRules,
  type LegalComparator,
  type LegalModality,
  type LegalReferenceType,
} from "../api/client";

const COMPARATOR_LABELS: Record<LegalComparator, string> = {
  gte: "以上",
  lte: "以下",
  lt: "未満",
  gt: "超える",
  eq: "=",
};

const MODALITY_LABELS: Record<LegalModality, string> = {
  obligation: "義務",
  prohibition: "禁止",
  permission: "許可",
  exception: "例外",
  definition: "定義",
};

const REF_TYPE_LABELS: Record<LegalReferenceType, string> = {
  article_citation: "条文引用",
  relative: "相対参照",
  external_law: "他法令引用",
  apply_mutatis_mutandis: "準用",
  deemed_reading: "読み替え",
};

type Props = {
  lawId: string;
  nodeId: string;
};

function SearchResultDetail({ lawId, nodeId }: Props) {
  const rulesQuery = useQuery({
    queryKey: ["legal-rules", lawId, nodeId],
    queryFn: () => getLegalRules(lawId, nodeId),
  });
  const referenceQuery = useQuery({
    queryKey: ["legal-reference", lawId, nodeId],
    queryFn: () => getLegalReference(lawId, nodeId),
  });

  return (
    <div className="legal-result-detail">
      <div className="legal-result-detail-section">
        <h4>抽出ルール(rule_graph)</h4>
        {rulesQuery.isLoading && <p className="hint">読み込み中...</p>}
        {rulesQuery.isError && <p className="error">{String(rulesQuery.error)}</p>}
        {rulesQuery.data && rulesQuery.data.length === 0 && (
          <p className="hint">このノードから抽出されたルールはありません。</p>
        )}
        {rulesQuery.data && rulesQuery.data.length > 0 && (
          <ul className="legal-rule-list">
            {rulesQuery.data.map((rule) => (
              <li key={rule.rule_id}>
                <div>
                  {rule.modality && (
                    <span className="tag">{MODALITY_LABELS[rule.modality] ?? rule.modality}</span>
                  )}
                  <span className="legal-rule-confidence">
                    確信度: {(rule.confidence * 100).toFixed(0)}%
                  </span>
                </div>
                {rule.conditions.length > 0 && (
                  <ul className="legal-condition-list">
                    {rule.conditions.map((condition, i) => (
                      <li key={i}>
                        「{condition.raw_text}」→ {condition.value}
                        {condition.unit ?? ""}
                        {condition.comparator && ` (${COMPARATOR_LABELS[condition.comparator]})`}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="legal-result-detail-section">
        <h4>引用関係(reference_graph)</h4>
        {referenceQuery.isLoading && <p className="hint">読み込み中...</p>}
        {referenceQuery.isError && <p className="error">{String(referenceQuery.error)}</p>}
        {referenceQuery.data && (
          <>
            <div className="legal-reference-group">
              <strong>この条文からの引用({referenceQuery.data.outgoing.length}件)</strong>
              {referenceQuery.data.outgoing.length === 0 ? (
                <p className="hint">なし</p>
              ) : (
                <ul className="legal-reference-list">
                  {referenceQuery.data.outgoing.map((edge, i) => (
                    <li key={i}>
                      <span className="tag">{REF_TYPE_LABELS[edge.ref_type] ?? edge.ref_type}</span>
                      {edge.raw_text}
                      {edge.resolved ? (
                        <span className="legal-ref-resolved"> ✓解決済み</span>
                      ) : (
                        <span className="legal-ref-unresolved">
                          {" "}
                          未解決({edge.unresolved_reason ?? "理由不明"})
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="legal-reference-group">
              <strong>この条文への引用({referenceQuery.data.incoming.length}件)</strong>
              {referenceQuery.data.incoming.length === 0 ? (
                <p className="hint">なし</p>
              ) : (
                <ul className="legal-reference-list">
                  {referenceQuery.data.incoming.map((edge, i) => (
                    <li key={i}>
                      <span className="tag">{REF_TYPE_LABELS[edge.ref_type] ?? edge.ref_type}</span>
                      {edge.raw_text}(引用元: {edge.from_node_id})
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default SearchResultDetail;
