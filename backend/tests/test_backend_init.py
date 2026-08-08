from backend import main


def test_main_prints_hello(capsys):
    # pyproject.tomlの[project.scripts] (`uv run backend`)から呼ばれる
    # CLIエントリポイント。実際のアプリはuvicorn backend.main:appで
    # 起動するため通常は使われないが、コマンドとして生きている以上は
    # クラッシュしないことを確認しておく。
    main()

    assert capsys.readouterr().out == "Hello from backend!\n"
