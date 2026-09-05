import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Vite 5.4+のデフォルトはHostヘッダがlocalhost系以外だと
    // "Blocked request. This host is not allowed"で拒否する(DNS
    // rebinding対策)。EC2をリバースプロキシ(Caddy)経由で実ドメイン
    // (例: bim-aiagent.com)で公開する場合、ブラウザからのHostヘッダは
    // そのドメインになるため、ここに明示的に許可しないとdocker-composeで
    // `--host 0.0.0.0`を渡していても本番ドメイン経由のアクセスは全て
    // ブロックされる。VITE_ALLOWED_HOSTSが未設定ならローカル開発時の
    // 挙動(localhost系のみ許可)を変えない。
    allowedHosts: process.env.VITE_ALLOWED_HOSTS
      ? process.env.VITE_ALLOWED_HOSTS.split(',').map((h) => h.trim())
      : undefined,
  },
})
