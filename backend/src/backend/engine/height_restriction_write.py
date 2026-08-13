"""高さ制限のenvelope(道路斜線/隣地斜線/北側斜線/高度地区、engine/
*_slant_envelope.py・engine/height_district_envelope.py)をArchicadへMesh
として書き込む、許可制の書き込みフロー(2026-08-13追加)。

CLAUDE.md「⑩ アクション/操作層」に記載の通り、既存の書き込み系Tapir
ラッパー(move_archicad_element等)には監査ログが無く、これがAIエージェント
へ書き込み権限を与えていない理由だった。今回、BIMモデルへ実際に書き込む
機能(高さ制限の可視化)を初めて追加するにあたり、CLAUDE.mdが前提としていた
監査ログ(database.db.write_audit_logテーブル)をあわせて導入した。

書き込みは2段階の明示的な承認フローを取る(「許可制」):
  1. propose_*_envelope_mesh(): envelopeを計算し、Archicadへはまだ一切
     書き込まず、write_audit_logへstatus="proposed"として記録するだけ
     (敷地ごとにproposal_idを発行する)。4種のenvelope(道路斜線/隣地斜線/
     北側斜線/高度地区)はいずれも{site_guid, site_name, resolved, vertices:
     [{x,y,z_m}]}という共通の形を持つため、_propose_envelope_mesh()に
     集約している。
  2. approve_envelope_mesh(proposal_id): 指定proposal_idの提案を取り出し、
     実際にTapirのCreateMeshesを呼んでArchicadへ書き込む。成功すれば
     status="written"+result_guid、失敗すればstatus="failed"+error_message
     を記録する。存在しない/既に処理済み(status!="proposed")のproposal_id
     はValueErrorにする(同じ提案の二重書き込み・取り消し済み提案の書き込み
     を防ぐ)。この関数はenvelopeの種類を一切気にしない(監査ログに保存
     された頂点データをそのままMeshにするだけ)ため、4種いずれの提案にも
     共通で使う(main.pyのエンドポイントも/engine/height_restrictions/approve
     1本を4種共通の承認窓口として使い回している)。

**このモジュールはAIエージェント(agent/tools.py)には公開しない。** 監査
ログの追加は「誰が/いつ/何を」を記録可能にすることが目的であり、AIエージェ
ントへの自律的な書き込み権限付与を意味しない(CLAUDE.md「⑨」の既存方針
——書き込み系ツールは監査ログ整備後に回す——はそのまま維持する)。承認は
人間がfrontend(REST API経由)から明示的に行う。
"""

import json

from ..archicad_mcp import tapir
from ..database import db
from .adjacent_boundary_slant_envelope import calculate_adjacent_boundary_slant_envelope
from .height_district_envelope import calculate_height_district_envelope
from .north_slant_envelope import calculate_north_slant_envelope
from .road_slant_envelope import calculate_road_slant_envelope

ACTION_ROAD_SLANT_ENVELOPE_MESH = "create_road_slant_envelope_mesh"
ACTION_ADJACENT_BOUNDARY_SLANT_ENVELOPE_MESH = "create_adjacent_boundary_slant_envelope_mesh"
ACTION_NORTH_SLANT_ENVELOPE_MESH = "create_north_slant_envelope_mesh"
ACTION_HEIGHT_DISTRICT_ENVELOPE_MESH = "create_height_district_envelope_mesh"


def _propose_envelope_mesh(action: str, label: str, envelopes: list[dict]) -> dict:
    """envelopes(calculate_*_envelope()の戻り値)のうちresolved=Trueな敷地
    それぞれについて、write_audit_logへstatus="proposed"として記録する
    (Archicadへは一切書き込まない、道路/隣地/北側3種のenvelopeで共通の処理)。
    """
    proposals = []
    for envelope in envelopes:
        if not envelope["resolved"]:
            continue

        summary = (
            f"{envelope['site_name']}(guid={envelope['site_guid']})に"
            f"{label}のenvelope(頂点{len(envelope['vertices'])}件)を"
            "Meshとして作成する提案"
        )
        proposal_id = db.insert_audit_log_proposal(
            action, summary, json.dumps(envelope, ensure_ascii=False)
        )
        proposals.append({"proposal_id": proposal_id, "summary": summary, "envelope": envelope})

    return {"proposals": proposals, "envelopes": envelopes}


def propose_road_slant_envelope_mesh(land_use_category: str | None = None) -> dict:
    """道路斜線制限(建築基準法56条1項1号)のenvelopeを提案として記録する。"""
    envelopes = calculate_road_slant_envelope(land_use_category)
    return _propose_envelope_mesh(ACTION_ROAD_SLANT_ENVELOPE_MESH, "道路斜線制限", envelopes)


def propose_adjacent_boundary_slant_envelope_mesh(land_use_category: str | None = None) -> dict:
    """隣地斜線制限(建築基準法56条1項2号)のenvelopeを提案として記録する。"""
    envelopes = calculate_adjacent_boundary_slant_envelope(land_use_category)
    return _propose_envelope_mesh(
        ACTION_ADJACENT_BOUNDARY_SLANT_ENVELOPE_MESH, "隣地斜線制限", envelopes
    )


def propose_north_slant_envelope_mesh(
    north_degrees: float, kitagawa_shasen_kubun: str | None = None
) -> dict:
    """北側斜線制限(建築基準法56条1項3号)のenvelopeを提案として記録する。

    north_degreesは呼び出し側(main.pyのエンドポイント)がArchicadの
    GetGeoLocationへ都度問い合わせて解決する(engine/north_slant_envelope.py
    のモジュールdocstring参照)。
    """
    envelopes = calculate_north_slant_envelope(north_degrees, kitagawa_shasen_kubun)
    return _propose_envelope_mesh(ACTION_NORTH_SLANT_ENVELOPE_MESH, "北側斜線制限", envelopes)


def propose_height_district_envelope_mesh(
    north_degrees: float | None = None,
    kubun: str | None = None,
    max_height_m: float | None = None,
    rise_m: float | None = None,
    gradient: float | None = None,
    kanwa_m: float | None = None,
) -> dict:
    """高度地区(都市計画法8条1項3号)のenvelopeを提案として記録する。

    north_degreesはkubun="north_slant"の場合のみ必要(呼び出し側が
    Archicadの GetGeoLocationへ都度問い合わせて解決する、engine/
    height_district_envelope.pyのモジュールdocstring参照)。
    """
    envelopes = calculate_height_district_envelope(
        north_degrees, kubun, max_height_m, rise_m, gradient, kanwa_m
    )
    return _propose_envelope_mesh(ACTION_HEIGHT_DISTRICT_ENVELOPE_MESH, "高度地区", envelopes)


async def approve_envelope_mesh(proposal_id: int) -> dict:
    """指定proposal_idの提案を承認し、実際にArchicadへMeshを書き込む。

    道路斜線/隣地斜線/北側斜線のいずれの提案にも共通で使える(envelopeの
    種類を判別する必要が無い、モジュールdocstring参照)。未承認の提案
    (status="proposed")のみ実行できる。存在しない、または既に処理済み
    (written/failed)のproposal_idはValueErrorにする。
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


# 旧名(道路斜線制限専用の提案しか無かった頃の名前)。approve_envelope_mesh()
# は元々envelopeの種類を判別しない実装だったため、動作は変えず名前だけの
# エイリアスとして残す(main.py・既存テストとの後方互換のため)。
approve_road_slant_envelope_mesh = approve_envelope_mesh


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
