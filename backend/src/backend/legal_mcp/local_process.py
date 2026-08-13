"""Legal Knowledge Builder(`~/Legal Knowledge Builder/`)をbackendと同じホスト上で
サブプロセスとしてローカル起動するための補助機能。

Archicad連携のWindows側ブリッジ(`archicad_mcp/`、CLAUDE.md「制約・方針」参照)は
本番でbackendとは別の(リモートの)Windows PC上で動くため、backend側から起動する
仕組みを意図的に作っていない。Legal Knowledge Builderはそれとは前提が異なり、
`legal_mcp/client.py`のLOCAL_PRESET_URL(127.0.0.1:8100)が示す通りbackendプロセスと
同じホスト上で動かす運用(開発時は同じWSL環境)を前提にしているため、backend
プロセスからのサブプロセス起動として実装できる(別ホストへのリモートコード実行
ではない)。起動コマンドは固定(`uv run legal-knowledge-builder serve`)で、
ユーザー入力をコマンドに組み込まない。

本番(AWS+Tailscale経由でbackendが動く構成)にLegal Knowledge Builderリポジトリが
無い場合はget_repo_dir()がNoneを返し、start_server()がLocalServerNotFoundErrorを
送出する——この機能はローカル開発での利便性のためのものであり、本番での動作は
想定していない。
"""

import os
import subprocess
from pathlib import Path

_DEFAULT_REPO_DIR = Path.home() / "Legal Knowledge Builder"

# 起動したサブプロセスのハンドル。backend再起動でリセットされる
# (archicad_mcp/client.pyのランタイムオーバーライドと同じ「プロセス内だけの状態」)。
_process: subprocess.Popen | None = None


class LocalServerNotFoundError(RuntimeError):
    """Legal Knowledge Builderのリポジトリがこのホスト上に見つからない場合に送出する。"""


def get_repo_dir() -> Path | None:
    """リポジトリの場所を解決する。環境変数LEGAL_KNOWLEDGE_BUILDER_DIRで上書き可能。"""

    override = os.environ.get("LEGAL_KNOWLEDGE_BUILDER_DIR")
    candidate = Path(override).expanduser() if override else _DEFAULT_REPO_DIR

    if candidate.is_dir() and (candidate / "pyproject.toml").is_file():
        return candidate

    return None


def is_process_alive() -> bool:
    return _process is not None and _process.poll() is None


def get_status() -> dict:
    return {
        "process_alive": is_process_alive(),
        "pid": _process.pid if is_process_alive() else None,
        "repo_dir": str(get_repo_dir()) if get_repo_dir() else None,
    }


def start_server() -> dict:
    """`uv run legal-knowledge-builder serve`をバックグラウンドで起動する。

    起動自体は即座に返る(ポートのbindや埋め込みモデルのロード完了までは
    待たない)。実際に使えるようになったかはGET /legal/statusで別途確認する
    (初回起動時はHuggingFaceからの埋め込みモデルダウンロードで数十秒〜数分
    かかることがある)。
    """

    global _process

    if is_process_alive():
        return {"started": False, "already_running": True, "pid": _process.pid}

    repo_dir = get_repo_dir()
    if repo_dir is None:
        raise LocalServerNotFoundError(
            "Legal Knowledge Builderのリポジトリが見つかりません"
            f"(既定の場所: {_DEFAULT_REPO_DIR}。環境変数LEGAL_KNOWLEDGE_BUILDER_DIRでも"
            "指定できます)。backendと同じホストに無い場合(本番のAWSデプロイ等)は"
            "この機能は使えません。手動で`uv run legal-knowledge-builder serve`を"
            "起動してください。"
        )

    log_path = repo_dir / ".legal_knowledge_builder_serve.log"

    log_file = open(log_path, "a")
    try:
        _process = subprocess.Popen(
            ["uv", "run", "legal-knowledge-builder", "serve"],
            cwd=str(repo_dir),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        # Popen()呼び出し時点で子プロセスは既にこのfdを引き継いでいるため、
        # 親側のPythonファイルオブジェクトはここで閉じてよい。
        log_file.close()

    return {
        "started": True,
        "already_running": False,
        "pid": _process.pid,
        "log_path": str(log_path),
    }
