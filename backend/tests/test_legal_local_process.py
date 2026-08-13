"""legal_mcp/local_process.pyのテスト。

subprocess.Popenの実呼び出しはfakeに差し替える(実際にuv/legal-knowledge-builder
バイナリを起動するとテスト環境依存になり、しかも長時間プロセスが残ってしまうため)。
"""

import pytest

from backend.legal_mcp import local_process


class _FakePopen:
    def __init__(self, pid=12345):
        self.pid = pid
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def kill_for_test(self):
        self._alive = False


def _make_repo(tmp_path):
    repo = tmp_path / "Legal Knowledge Builder"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = \"legal-knowledge-builder\"\n")
    return repo


def test_get_repo_dir_returns_none_when_env_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("LEGAL_KNOWLEDGE_BUILDER_DIR", str(tmp_path / "does-not-exist"))

    assert local_process.get_repo_dir() is None


def test_get_repo_dir_returns_none_without_pyproject_toml(monkeypatch, tmp_path):
    repo = tmp_path / "empty-dir"
    repo.mkdir()
    monkeypatch.setenv("LEGAL_KNOWLEDGE_BUILDER_DIR", str(repo))

    assert local_process.get_repo_dir() is None


def test_get_repo_dir_resolves_env_override(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    monkeypatch.setenv("LEGAL_KNOWLEDGE_BUILDER_DIR", str(repo))

    assert local_process.get_repo_dir() == repo


def test_start_server_raises_when_repo_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("LEGAL_KNOWLEDGE_BUILDER_DIR", str(tmp_path / "nope"))

    with pytest.raises(local_process.LocalServerNotFoundError):
        local_process.start_server()


def test_start_server_spawns_fixed_command_in_repo_dir(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    monkeypatch.setenv("LEGAL_KNOWLEDGE_BUILDER_DIR", str(repo))

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return _FakePopen()

    monkeypatch.setattr(local_process.subprocess, "Popen", fake_popen)

    result = local_process.start_server()

    assert result == {
        "started": True,
        "already_running": False,
        "pid": 12345,
        "log_path": str(repo / ".legal_knowledge_builder_serve.log"),
    }
    # ユーザー入力の混入余地が無い固定コマンドであることを確認する
    # (main.pyのエンドポイントは引数を取らないPOSTなので、そもそも
    # ユーザー入力は届かないが、コマンド自体が固定であることも直接検証する)。
    assert captured["cmd"] == ["uv", "run", "legal-knowledge-builder", "serve"]
    assert captured["cwd"] == str(repo)


def test_start_server_does_not_spawn_again_when_already_running(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    monkeypatch.setenv("LEGAL_KNOWLEDGE_BUILDER_DIR", str(repo))

    call_count = 0

    def fake_popen(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        return _FakePopen()

    monkeypatch.setattr(local_process.subprocess, "Popen", fake_popen)

    first = local_process.start_server()
    second = local_process.start_server()

    assert call_count == 1
    assert first["already_running"] is False
    assert second == {"started": False, "already_running": True, "pid": 12345}


def test_start_server_spawns_again_after_process_exits(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    monkeypatch.setenv("LEGAL_KNOWLEDGE_BUILDER_DIR", str(repo))

    fake_processes = [_FakePopen(pid=1), _FakePopen(pid=2)]

    def fake_popen(cmd, **kwargs):
        return fake_processes.pop(0)

    monkeypatch.setattr(local_process.subprocess, "Popen", fake_popen)

    first = local_process.start_server()
    local_process._process.kill_for_test()
    second = local_process.start_server()

    assert first["pid"] == 1
    assert second["pid"] == 2
    assert second["already_running"] is False


def test_get_status_reports_process_alive_and_repo_dir(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    monkeypatch.setenv("LEGAL_KNOWLEDGE_BUILDER_DIR", str(repo))
    monkeypatch.setattr(local_process.subprocess, "Popen", lambda cmd, **kwargs: _FakePopen())

    assert local_process.get_status() == {
        "process_alive": False,
        "pid": None,
        "repo_dir": str(repo),
    }

    local_process.start_server()

    status = local_process.get_status()
    assert status["process_alive"] is True
    assert status["pid"] == 12345
