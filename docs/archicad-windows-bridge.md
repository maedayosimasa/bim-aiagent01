# Archicad MCPブリッジ構築手順(Windowsネイティブ + Tailscale)

archicad-mcp(`C:\Users\<user>\archicad-mcp`)をWindowsネイティブで常駐HTTPサーバーとして動かし、
Tailscale経由でAWS上のbackendから接続できるようにする手順。

前提: Archicad 28 + Tapir Add-Onは導入済み、`uv run python -c "from archicad import ACConnection; c = ACConnection.connect(); print(c.version, c.build)"`
がPowerShellから成功することを確認済み(`28 3001`のような出力)。

## クイックセットアップ(複数PCへの配布用、2026-09-05追加)

新しいPCでこのブリッジを使う人には、以下の手順(1〜4)を1つずつ説明する代わりに、
[`setup_archicad_bridge.ps1`](./setup_archicad_bridge.ps1)を配布して1回実行してもらうだけでよい。

1. (管理者側)Tailscale管理コンソール(https://login.tailscale.com/admin/settings/keys)で
   再利用可能(Reusable)な認証キーを発行し、配布する人に渡す(社内で使い回してよい)。
2. (配布先PC)archicad-mcp・Archicad・Tapir Add-On・uvがセットアップ済みであることを確認した上で、
   管理者権限のPowerShellで以下を実行する:
   ```powershell
   .\setup_archicad_bridge.ps1 -TailscaleAuthKey "tskey-auth-xxxxxxxx"
   ```
3. スクリプトの最後に表示される`http://<Tailscale IP>:8765/mcp/`を、
   https://bim-aiagent.com/ のダッシュボード→Archicad接続→「カスタムURL」に貼り付けて
   「この接続に切り替える」を押す。

以下の1〜4は、上記スクリプトが内部で自動実行している内容の詳細(トラブルシュート時の参照用)。

## 1. HTTP起動用スクリプトを追加する

既存の`server.py`(Claude Desktopのstdio設定が参照している)は**変更しない**。
同じディレクトリに`server_http.py`を新規作成し、同じ`mcp`オブジェクトを
別のtransportで起動する。

```python
# archicad-mcp/src/server_http.py
from server import mcp

if __name__ == "__main__":
    mcp.run(
        transport="http",       # FastMCP 2.xでは "http" が推奨名("streamable-http"のエイリアス)
        host="0.0.0.0",         # Tailscale経由でも受け付けるため127.0.0.1から変更
        port=8765,
            )
```

fastmcpの`run()`実際のシグネチャ(`fastmcp>=2.11.3`で確認済み):
```
run(transport=None, show_banner=None, **transport_kwargs)
run_http_async(..., host=None, port=None, host_origin_protection=None, allowed_hosts=None, ...)
```
`host_origin_protection`の既定値は`False`(公式`mcp` SDKと違い、Host/Originヘッダーの
制限は既定で無効)なので、docker-compose内サービス名や外部ホスト名からのアクセスで
弾かれる心配は基本的に無い。

動作確認(PowerShell、Claude Desktopとは別プロセスとして手動起動):
```powershell
cd C:\Users\<user>\archicad-mcp
$env:PYTHONPATH = "C:\Users\<user>\archicad-mcp\src"
uv run python -m server_http
```
起動後、別ターミナルから:
```powershell
curl http://127.0.0.1:8765/mcp/
```
何らかのレスポンス(MCPのエラーでも可)が返れば起動成功。

## 2. 常駐化(タスクスケジューラ、追加インストール不要)

Claude Desktop実行中だけでなく、PCが起動していれば常に動いている状態にする。
Windows標準のタスクスケジューラを使えばNSSM等の追加ツールは不要。

まず、環境変数設定とuv起動をまとめたラッパースクリプトを作成する。

```powershell
# C:\Users\<user>\archicad-mcp\start_http.ps1
$env:PYTHONPATH = "C:\Users\<user>\archicad-mcp\src"
Set-Location "C:\Users\<user>\archicad-mcp"
uv run python -m server_http
```

管理者権限のPowerShellで以下を実行し、タスクを登録する。

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\Users\<user>\archicad-mcp\start_http.ps1"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "ArchicadMcpHttp" -Action $action `
  -Trigger $trigger -Settings $settings -RunLevel Highest
```

- `-ExecutionTimeLimit ([TimeSpan]::Zero)`: タスクスケジューラは既定で72時間で
  タスクを強制終了するため、無制限に設定する(無いと3日でサーバーが落ちる)。
- `-AtLogOn`: ログオン時に起動。Archicad自体もGUIアプリで対話ログインが必要な
  ので、この起動タイミングが実運用と合っている。
- `-RestartCount 3 -RestartInterval ...`: この2つを指定するだけで
  再起動ポリシーが有効になる(単独の`-Restart`スイッチは存在しない)。

即座に起動確認したい場合は、タスクスケジューラGUI(`taskschd.msc`)で
「ArchicadMcpHttp」を右クリック→「実行」。

補足: このサービスはArchicad自体が起動していないと`ACConnection.connect()`が
失敗するツール呼び出しになる(サービス自体は起動できる)。Archicadを開いてから
使う運用を前提とする。

## 3. Tailscaleのインストール(Windows)

1. https://tailscale.com/download/windows からインストーラを取得しインストール
2. サインインして同じtailnetに参加させる(AWS側のtailnet加入方法は別途 [docker-compose.yml](../docker-compose.yml) の`tailscale`サービス参照)
3. Tailscale管理コンソールでこのPCのマシン名を確認(例: `pc-archicad`)
4. (推奨)ACLでAWS側ノードからこのPCの8765番ポートのみアクセス許可に絞る

## 4. HTTPS化(推奨、任意)

```powershell
tailscale cert pc-archicad.<tailnet-name>.ts.net
```
発行された証明書をリバースプロキシ(Caddy等)でTLS終端し、`server_http.py`側は
`127.0.0.1`のみで待ち受けるように変更すると、tailnet外に生のHTTPを晒さずに済む。
最低限の構成であれば、Tailscale自体がWireGuardで暗号化しているため、
HTTP直結でも通信内容自体は保護されている(詳細は過去のやり取り参照)。

## 5. AWS backend側の接続確認

`.env`(リポジトリルート)に設定:
```
ARCHICAD_MCP_URL=http://pc-archicad.<tailnet-name>.ts.net:8765/mcp/
```
(HTTPS化した場合は`https://`)

docker-composeを再起動後、以下で確認:
```bash
curl http://<AWS側ホスト>:8000/archicad/status
```

期待される結果:
```json
{"configured": true, "reachable": true, "tools": ["<Tapirの実際のツール名...>"]}
```

`"reachable": false`の場合は`"error"`キーに具体的な理由(接続タイムアウト等)が
入るので、それを見てTailscaleの接続状況・PC側サービスの起動状況を確認する。
