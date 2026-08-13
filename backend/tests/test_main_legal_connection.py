def test_get_connection_defaults_to_env_var(api_client, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)

    response = api_client.get("/legal/connection")

    assert response.status_code == 200
    body = response.json()
    assert body["active_url"] is None
    assert body["overridden"] is False


def test_post_local_switches_to_local_preset(api_client, monkeypatch):
    # 疎通確認自体は legal_client.check_connection() 側の別テストで検証済み。
    # ここでは接続先切り替えのロジックだけを見たいので、実ネットワークには
    # 依存させない(開発機でたまたま8100番に何か立っていても結果が揺れないように)。
    from backend.legal_mcp import client as legal_client

    async def fake_check_connection():
        return {"configured": True, "reachable": False, "error": "connection refused"}

    monkeypatch.setattr(legal_client, "check_connection", fake_check_connection)

    response = api_client.post("/legal/connection", json={"url": "local"})

    assert response.status_code == 200
    body = response.json()
    assert body["connection"]["active_url"] == "http://127.0.0.1:8100"
    assert body["connection"]["overridden"] is True
    assert body["status"]["configured"] is True
    assert body["status"]["reachable"] is False


def test_post_null_resets_to_env_var(api_client, monkeypatch):
    monkeypatch.setenv("LEGAL_API_URL", "https://legal.example.invalid")

    api_client.post("/legal/connection", json={"url": "local"})
    response = api_client.post("/legal/connection", json={"url": None})

    body = response.json()
    assert body["connection"]["active_url"] == "https://legal.example.invalid"
    assert body["connection"]["overridden"] is False


def test_post_custom_url_is_used_verbatim(api_client):
    response = api_client.post("/legal/connection", json={"url": "http://example.invalid"})

    body = response.json()
    assert body["connection"]["active_url"] == "http://example.invalid"


def test_status_not_configured(api_client, monkeypatch):
    monkeypatch.delenv("LEGAL_API_URL", raising=False)
    api_client.post("/legal/connection", json={"url": None})

    response = api_client.get("/legal/status")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["reachable"] is False


class _FakePopen:
    def __init__(self, pid=12345):
        self.pid = pid

    def poll(self):
        return None


def test_start_server_endpoint_spawns_and_sets_local_connection(
    api_client, monkeypatch, tmp_path
):
    from backend.legal_mcp import local_process as legal_local_process

    repo = tmp_path / "Legal Knowledge Builder"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = \"x\"\n")
    monkeypatch.setenv("LEGAL_KNOWLEDGE_BUILDER_DIR", str(repo))
    monkeypatch.delenv("LEGAL_API_URL", raising=False)
    monkeypatch.setattr(
        legal_local_process.subprocess, "Popen", lambda cmd, **kwargs: _FakePopen()
    )

    response = api_client.post("/legal/start_server")

    assert response.status_code == 200
    body = response.json()
    assert body["started"] is True
    assert body["pid"] == 12345
    # LEGAL_API_URLが未設定だったので、ローカルプリセットに自動で合わせる
    # (セットで使う想定のため、起動後に別途接続先を手動設定させない)。
    assert body["connection"]["active_url"] == "http://127.0.0.1:8100"


def test_start_server_endpoint_does_not_override_existing_connection(
    api_client, monkeypatch, tmp_path
):
    from backend.legal_mcp import local_process as legal_local_process

    repo = tmp_path / "Legal Knowledge Builder"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = \"x\"\n")
    monkeypatch.setenv("LEGAL_KNOWLEDGE_BUILDER_DIR", str(repo))
    monkeypatch.setattr(
        legal_local_process.subprocess, "Popen", lambda cmd, **kwargs: _FakePopen()
    )

    api_client.post("/legal/connection", json={"url": "https://legal.example.invalid"})

    response = api_client.post("/legal/start_server")

    body = response.json()
    assert body["connection"]["active_url"] == "https://legal.example.invalid"


def test_start_server_endpoint_404_repo_missing_returns_400(api_client, monkeypatch, tmp_path):
    monkeypatch.setenv("LEGAL_KNOWLEDGE_BUILDER_DIR", str(tmp_path / "nope"))

    response = api_client.post("/legal/start_server")

    assert response.status_code == 400
    assert "見つかりません" in response.json()["detail"]


def test_start_server_status_endpoint(api_client, monkeypatch, tmp_path):
    repo = tmp_path / "Legal Knowledge Builder"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = \"x\"\n")
    monkeypatch.setenv("LEGAL_KNOWLEDGE_BUILDER_DIR", str(repo))

    response = api_client.get("/legal/start_server/status")

    assert response.status_code == 200
    body = response.json()
    assert body["process_alive"] is False
    assert body["repo_dir"] == str(repo)
