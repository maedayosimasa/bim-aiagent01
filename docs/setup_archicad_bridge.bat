@echo off
setlocal

rem ============================================================
rem  Archicad MCPブリッジ かんたんセットアップ
rem
rem  setup_archicad_bridge.ps1 を管理者権限のPowerShellで自分で
rem  開いて実行する手間を無くすための、ダブルクリック起動用ラッパー。
rem  このファイルと setup_archicad_bridge.ps1 を同じフォルダに置いて
rem  配布すること(この.batだけでは動作しない)。
rem
rem  注意: ファイアウォールルール・タスクスケジューラ登録には
rem  Windowsの管理者権限が必須のため、実行時に「このアプリがデバイスに
rem  変更を加えることを許可しますか?」という確認(UAC)が表示される。
rem  これはWindowsの仕様上避けられないが、「はい」を押すだけでよい。
rem ============================================================

rem 管理者権限で実行されているか確認し、なければ自分自身を管理者として
rem 再起動する(net session は管理者でないと失敗することを利用した判定)。
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo 管理者権限で再起動します。確認ダイアログが出たら「はい」を押してください...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set SCRIPT_DIR=%~dp0
set PS1_PATH=%SCRIPT_DIR%setup_archicad_bridge.ps1

if not exist "%PS1_PATH%" (
    echo エラー: setup_archicad_bridge.ps1 が見つかりません。
    echo この.batファイルと同じフォルダに setup_archicad_bridge.ps1 を置いてください。
    pause
    exit /b 1
)

echo ============================================
echo  Archicad MCPブリッジ セットアップ
echo ============================================
echo.
echo Tailscaleの認証キー(tskey-auth-... の形式)を、
echo 配布元(管理者)から受け取って入力してください。
echo.
set /p TAILSCALE_AUTH_KEY="認証キー: "

if "%TAILSCALE_AUTH_KEY%"=="" (
    echo 認証キーが入力されませんでした。処理を中止します。
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1_PATH%" -TailscaleAuthKey "%TAILSCALE_AUTH_KEY%"

echo.
echo 上に表示された http://... のURLを、
echo https://bim-aiagent.com/ のダッシュボード → Archicad接続 → 「カスタムURL」に
echo 貼り付けて「この接続に切り替える」を押してください。
echo.
pause
