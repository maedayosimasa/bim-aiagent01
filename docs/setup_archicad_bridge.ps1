<#
.SYNOPSIS
  archicad-mcpをHTTPブリッジとして常駐させ、TailscaleでAWS側backendから
  到達できるようにする配布用セットアップスクリプト。

.DESCRIPTION
  docs/archicad-windows-bridge.md の手順(HTTP起動用スクリプト作成→
  タスクスケジューラ登録→Tailscale参加→ファイアウォール開放)を
  1本のスクリプトにまとめたもの。新しいPCでArchicadを使う人に配布し、
  管理者権限のPowerShellで1回実行してもらうだけで済むようにする。

  前提条件(このスクリプトでは自動化しない、事前に済ませておくこと):
    - Archicad 28 + Tapir Add-On が導入済み
    - archicad-mcp(https://github.com/ENZYME-APD/tapir-archicad-automation 等、
      社内で使っているarchicad-mcp一式)が -ArchicadMcpDir に展開済みで、
      既存のserver.py(Claude Desktop用のstdio版)が動作確認済みであること
    - uv (https://docs.astral.sh/uv/) がインストール済みであること

  実行例:
    .\setup_archicad_bridge.ps1 -TailscaleAuthKey "tskey-auth-xxxxxxxx"

  -ArchicadMcpDir を省略した場合 既定は "$env:USERPROFILE\archicad-mcp"。

.PARAMETER TailscaleAuthKey
  Tailscale管理コンソール(https://login.tailscale.com/admin/settings/keys)
  で発行した再利用可能(Reusable)な認証キー。配布する人ごとに新規発行しても、
  既存の再利用可能キーを使い回してもよい(社内配布用に、有効期限を長め・
  デバイス数上限を必要数に設定したキーを1つ発行しておくことを推奨)。
  backend(EC2)のtailscaleサイドカーが使っているTS_AUTHKEYとは別物として
  扱ってよい(同じキーの使い回しも可、tailnetが同じであれば問題ない)。

.PARAMETER ArchicadMcpDir
  archicad-mcpを展開してあるディレクトリ。既定: "$env:USERPROFILE\archicad-mcp"

.PARAMETER Port
  HTTPブリッジの待受ポート。既定: 8765(backend側の既定値と合わせること)
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TailscaleAuthKey,

    [string]$ArchicadMcpDir = "$env:USERPROFILE\archicad-mcp",

    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
        throw "管理者権限のPowerShellで実行してください(右クリック→「管理者として実行」)。"
    }
}

Write-Host "=== 1/5: 権限確認 ===" -ForegroundColor Cyan
Assert-Admin

Write-Host "=== 2/5: archicad-mcpディレクトリ確認 ===" -ForegroundColor Cyan
if (-not (Test-Path $ArchicadMcpDir)) {
    throw "archicad-mcpが見つかりません: $ArchicadMcpDir`n" +
          "先にarchicad-mcp一式を展開し、既存のserver.py(stdio版)が動作することを確認してください。"
}
if (-not (Test-Path (Join-Path $ArchicadMcpDir "src\server.py"))) {
    throw "src\server.py が見つかりません: $ArchicadMcpDir`n" +
          "-ArchicadMcpDir でarchicad-mcpの正しい展開先を指定してください。"
}

Write-Host "=== 3/5: HTTP起動用スクリプトを作成 ===" -ForegroundColor Cyan
# 既存のserver.py(Claude Desktopのstdio設定が参照している)は変更せず、
# 同じmcpオブジェクトを別transportで起動するラッパーを追加する
# (docs/archicad-windows-bridge.md「1. HTTP起動用スクリプトを追加する」参照)。
$serverHttpPath = Join-Path $ArchicadMcpDir "src\server_http.py"
$serverHttpContent = @"
from server import mcp

if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=$Port,
    )
"@
Set-Content -Path $serverHttpPath -Value $serverHttpContent -Encoding UTF8
Write-Host "作成: $serverHttpPath"

$startScriptPath = Join-Path $ArchicadMcpDir "start_http.ps1"
$startScriptContent = @"
`$env:PYTHONPATH = "$ArchicadMcpDir\src"
Set-Location "$ArchicadMcpDir"
uv run python -m server_http
"@
Set-Content -Path $startScriptPath -Value $startScriptContent -Encoding UTF8
Write-Host "作成: $startScriptPath"

Write-Host "=== 4/5: タスクスケジューラへ登録(ArchicadMcpHttp) ===" -ForegroundColor Cyan
# 再実行に備え、既存の同名タスクがあれば一旦削除してから登録し直す(冪等化)。
$existingTask = Get-ScheduledTask -TaskName "ArchicadMcpHttp" -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "既存タスクを削除して再登録します。"
    Unregister-ScheduledTask -TaskName "ArchicadMcpHttp" -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startScriptPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "ArchicadMcpHttp" -Action $action `
    -Trigger $trigger -Settings $settings -RunLevel Highest | Out-Null
Write-Host "タスク「ArchicadMcpHttp」を登録しました(ログオン時に自動起動)。"

Write-Host "=== 5/5: Windowsファイアウォールでポート$Portを開放 ===" -ForegroundColor Cyan
$existingRule = Get-NetFirewallRule -DisplayName "ArchicadMcpHttp" -ErrorAction SilentlyContinue
if (-not $existingRule) {
    New-NetFirewallRule -DisplayName "ArchicadMcpHttp" -Direction Inbound `
        -Protocol TCP -LocalPort $Port -Action Allow -Profile Any | Out-Null
    Write-Host "ファイアウォールルールを追加しました。"
} else {
    Write-Host "ファイアウォールルールは既に存在します(スキップ)。"
}

Write-Host "=== Tailscaleのインストール確認・tailnet参加 ===" -ForegroundColor Cyan
$tailscaleExe = Get-Command tailscale.exe -ErrorAction SilentlyContinue
if (-not $tailscaleExe) {
    $defaultPath = "C:\Program Files\Tailscale\tailscale.exe"
    if (Test-Path $defaultPath) {
        $tailscaleExe = $defaultPath
    } else {
        Write-Host "Tailscaleが見つかりません。winget でインストールを試みます..."
        try {
            winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
        } catch {
            throw "Tailscaleの自動インストールに失敗しました。" +
                  "https://tailscale.com/download/windows から手動でインストールしてから再実行してください。"
        }
        $tailscaleExe = $defaultPath
    }
}

& $tailscaleExe up --authkey $TailscaleAuthKey --hostname "archicad-$env:COMPUTERNAME"

Start-Sleep -Seconds 3
$tsIp = (& $tailscaleExe ip -4).Trim()

Write-Host ""
Write-Host "=== セットアップ完了 ===" -ForegroundColor Green
Write-Host "このPCのTailscale IP: $tsIp"
Write-Host ""
Write-Host "次の手順:" -ForegroundColor Yellow
Write-Host "1. Archicadを起動し、プロジェクトを開いた状態にしてください。"
Write-Host ("2. タスクスケジューラ(taskschd.msc)で「ArchicadMcpHttp」を右クリック→「実行」" `
    + "(次回以降はログオン時に自動起動します)。")
Write-Host ("3. https://bim-aiagent.com/ のダッシュボード→Archicad接続→「カスタムURL」に" `
    + "以下を入力し、「この接続に切り替える」を押してください:")
Write-Host ""
Write-Host "   http://${tsIp}:$Port/mcp/" -ForegroundColor Cyan
Write-Host ""
Write-Host "「接続完了」と表示されれば成功です。"
