"""道路斜線制限のenvelope(engine/road_slant_envelope.py)をArchicadへMesh
として書き込む、許可制の書き込みフロー(2026-08-13追加)。

CLAUDE.md「⑩ アクション/操作層」に記載の通り、既存の書き込み系Tapir
ラッパー(move_archicad_element等)には監査ログが無く、これがAIエージェント
へ書き込み権限を与えていない理由だった。今回、BIMモデルへ実際に書き込む
機能(高さ制限の可視化)を初めて追加するにあたり、CLAUDE.mdが前提としていた
監査ログ(database.db.write_audit_logテーブル)をあわせて導入した。

書き込みは2段階の明示的な承認フローを取る(「許可制」):
  1. propose_road_slant_envelope_mesh(): envelopeを計算し、Archicadへは
     まだ一切書き込まず、write_audit_logへstatus="proposed"として記録する
     だけ(敷地ごとにproposal_idを発行する)。
  2. approve_road_slant_envelope_mesh(proposal_id): 指定proposal_idの提案を
     取り出し、実際にTapirのCreateMeshesを呼んでArchicadへ書き込む。
     成功すればstatus="written"+result_guid、失敗すればstatus="failed"+
     error_messageを記録する。存在しない/既に処理済み(status!="proposed")
     のproposal_idはValueErrorにする(同じ提案の二重書き込み・取り消し済み
     提案の書き込みを防ぐ)。

**このモジュールはAIエージェント(agent/tools.py)には公開しない。** 監査
ログの追加は「誰が/いつ/何を」を記録可能にすることが目的であり、AIエージェ
ントへの自律的な書き込み権限付与を意味しない(CLAUDE.md「⑨」の既存方針
——書き込み系ツールは監査ログ整備後に回す——はそのまま維持する)。承認は
人間がfrontend(REST API経由)から明示的に行う。
"""

import json

from ..archicad_mcp import tapir
from ..database import db
from .road_slant_envelope import calculate_road_slant_envelope

ACTION_ROAD_SLANT_ENVELOPE_MESH = "create_road_slant_envelope_mesh"


def propose_road_slant_envelope_mesh(land_use_category: str | None = None) -> dict:
    """道路斜線制限のenvelopeを計算し、書き込み前の提案として監査ログに記録する。

    Archicadへは一切書き込まない(read-only計算+ログ記録のみ)。
    resolved=Falseの敷地(前面道路が見つからない等)は提案を作らない。
    """
    envelopes = calculate_road_slant_envelope(land_use_category)

    proposals = []
    for envelope in envelopes:
        if not envelope["resolved"]:
            continue

        summary = (
            f"{envelope['site_name']}(guid={envelope['site_guid']})に"
            f"道路斜線制限のenvelope(用途地域={envelope['land_use_category']}、"
            f"勾配={envelope['gradient']}、適用距離={envelope['applicable_distance_m']}m、"
            f"頂点{len(envelope['vertices'])}件)をMeshとして作成する提案"
        )
        proposal_id = db.insert_audit_log_proposal(
            ACTION_ROAD_SLANT_ENVELOPE_MESH,
            summary,
            json.dumps(envelope, ensure_ascii=False),
        )
        proposals.append({"proposal_id": proposal_id, "summary": summary, "envelope": envelope})

    return {"proposals": proposals, "envelopes": envelopes}


async def approve_road_slant_envelope_mesh(proposal_id: int) -> dict:
    """指定proposal_idの提案を承認し、実際にArchicadへMeshを書き込む。

    未承認の提案(status="proposed")のみ実行できる。存在しない、または
    既に処理済み(written/failed)のproposal_idはValueErrorにする。
    """
    entry = db.get_audit_log_entry(proposal_id)
    if entry is None:
        raise ValueError(f"存在しない提案IDです: {proposal_id}")
    if entry["status"] != "proposed":
        raise ValueError(
            f"この提案は既に処理済みです(status={entry['status']})。"
            "1つの提案につき書き込みを実行できるのは1回のみです。"
            "改めてenvelopeを計算し直して新しい提案を作成してください。"
        )

    envelope = json.loads(entry["payload_json"])
    vertices_mm = [
        {"x": v["x"], "y": v["y"], "z": v["z_m"] * 1000}
        for v in envelope["vertices"]
    ]

    try:
        result = await tapir.create_mesh(vertices_mm)
    except Exception as exc:
        db.mark_audit_log_failed(proposal_id, str(exc))
        raise

    result_guid = _extract_created_guid(result)
    db.mark_audit_log_written(proposal_id, result_guid)

    return {"proposal_id": proposal_id, "result_guid": result_guid, "raw_result": result}


def _extract_created_guid(result: dict) -> str | None:
    """CreateMeshesの戻り値({"elements": [{"elementId": {"guid": ...}}]}、
    archicad_mcp/tapir.pyのcreate_mesh()のdocstring参照)から作成された
    要素のguidを取り出す。想定と異なる形式の場合はNone(過検出よりも
    見逃しを許容する設計、他のTapir応答パース箇所と同じ)。
    """
    elements = result.get("elements") or []
    if not elements:
        return None
    return (elements[0].get("elementId") or {}).get("guid")
