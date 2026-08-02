def test_get_connection_defaults_to_env_var(api_client, monkeypatch):
    monkeypatch.delenv("ARCHICAD_MCP_URL", raising=False)

    response = api_client.get("/archicad/connection")

    assert response.status_code == 200
    body = response.json()
    assert body["active_url"] is None
    assert body["overridden"] is False


def test_post_local_switches_to_local_preset(api_client):
    response = api_client.post("/archicad/connection", json={"url": "local"})

    assert response.status_code == 200
    body = response.json()
    assert body["connection"]["active_url"] == "http://127.0.0.1:8765/mcp/"
    assert body["connection"]["overridden"] is True
    # Nothing is actually listening on 8765 in this environment, so the
    # switch itself must succeed even though the connectivity check fails.
    assert body["status"]["configured"] is True
    assert body["status"]["reachable"] is False


def test_post_null_resets_to_env_var(api_client, monkeypatch):
    monkeypatch.setenv("ARCHICAD_MCP_URL", "https://remote.example.invalid/mcp")

    api_client.post("/archicad/connection", json={"url": "local"})
    response = api_client.post("/archicad/connection", json={"url": None})

    body = response.json()
    assert body["connection"]["active_url"] == "https://remote.example.invalid/mcp"
    assert body["connection"]["overridden"] is False


def test_post_custom_url_is_used_verbatim(api_client):
    response = api_client.post(
        "/archicad/connection", json={"url": "http://example.invalid/mcp"}
    )

    body = response.json()
    assert body["connection"]["active_url"] == "http://example.invalid/mcp"
