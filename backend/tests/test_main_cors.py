"""CORS許可オリジンの解決(main.py `_resolve_cors_origins()`)を検証する。

CORSMiddlewareはFastAPIアプリのimport時に1度だけ構築されるため、
実際に構築されたミドルウェアの挙動をHTTP経由で確認するのではなく、
解決ロジック自体を純粋関数として切り出して単体テストする。

注意: `backend.main`はimport時に`_load_dotenv_if_present()`
(setdefaultでローカルの.envを読み込む)を実行する副作用を持つため、
他のテストファイルと同様にモジュールレベルではなく各テスト内で遅延
importする(モジュールレベルでimportすると、pytestのcollectionフェーズ
(全テスト実行前に全ファイルをimportする)で他のテストより先に.envが
読み込まれてしまい、他のテスト(LAND_USE_CATEGORY等の環境変数を前提と
するテスト)の分離を壊す実害が過去に確認されている)。
"""


def test_resolve_cors_origins_defaults_to_localhost_when_unset(monkeypatch):
    from backend.main import _resolve_cors_origins

    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)

    assert _resolve_cors_origins() == ["http://localhost:5173"]


def test_resolve_cors_origins_defaults_to_localhost_when_empty_string(monkeypatch):
    from backend.main import _resolve_cors_origins

    # docker-compose経由だと未設定時も`CORS_ALLOWED_ORIGINS=`(空文字列)として
    # 環境変数自体は存在する状態になるため、これも未設定と同じ扱いにする。
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")

    assert _resolve_cors_origins() == ["http://localhost:5173"]


def test_resolve_cors_origins_parses_single_origin(monkeypatch):
    from backend.main import _resolve_cors_origins

    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://bim.example.com")

    assert _resolve_cors_origins() == ["https://bim.example.com"]


def test_resolve_cors_origins_parses_comma_separated_multiple_origins(monkeypatch):
    from backend.main import _resolve_cors_origins

    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "https://bim.example.com, https://bim-staging.example.com ,",
    )

    assert _resolve_cors_origins() == [
        "https://bim.example.com",
        "https://bim-staging.example.com",
    ]
