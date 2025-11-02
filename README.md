# vrc-log-video-sync

VRChat のログ（`output_log_*.txt`）を監視し、再生中の動画（主に YouTube）をブラウザで同期表示するミニツールです。
VRChat上でエラーが発生して読み込めない動画でも、ログファイルを参照し同期表示します。
ローカル実行・Docker・Docker Compose・uvx（GitHub から直接）で動作します。

---

## 特長
- ログ自動検出（Windows）と最新ログ自動フォロー
- シンプルな Web UI（`/client`）と状態 API（`/state`）
- 既定はローカルのみで待受（`127.0.0.1`）。コンテナでは自動で外部公開
- 依存は実行時ゼロ（dev 依存は uv の dependency group で分離）

## 動作要件
- Windows（VRChat ログ監視想定）
- ブラウザ（YouTube IFrame API を使用）
- いずれかの実行方法を選択
  - Python 3.13 + [uv](https://astral.sh)（ローカル）
  - Docker / Docker Desktop（Windows）

---

## 実行方法

### 1) uvx（GitHub から実行／インストール不要）
GitHub 上のリポジトリから直接実行します。

```
uvx --python 3.13 --from git+https://github.com/attamari/vrc_log_video_sync.git vrc-log-video-sync --help
```

### 2) ローカル（uv）
```
# 依存を同期（実行時のみ）
uv sync --frozen
# 実行
uv run vrc-log-video-sync
```

開発ツールも使う場合（pyright/ruff）:
```
uv sync --group dev
uv run ruff check .
uv run pyright
```

### 3) Docker（単体）
```
docker build -t vrc-log-video-sync .
docker run --rm -p 7957:7957 \
  -v "C:\Users\<あなた>\AppData\LocalLow\VRChat\VRChat:/vrchat-logs:ro" \
  vrc-log-video-sync
```

### 4) Docker Compose（Windows）
```
# 初回のみ（任意）: 例をコピーして編集
copy .env.example .env

# そのままでも OK（USERPROFILE にフォールバック）
docker compose up -d --build
```

- 既定でホストの `%USERPROFILE%\AppData\LocalLow\VRChat\VRChat` を読み取り専用でマウント
- パスを変えたい場合は `.env` に `VRCHAT_LOG_DIR=...` を設定

---

## 使い方（共通）
サーバ起動後、ブラウザで以下を開きます。

- UI: `http://<host>:<port>/client`
- 状態 API: `http://<host>:<port>/state`

既定値:
- ローカル実行: `host=127.0.0.1`, `port=7957`
- Docker/Compose: `host=0.0.0.0`（コンテナ内）→ ホストからは `localhost:7957`

主なオプション（`--help` でも確認可能）:
- `--host`（既定: `127.0.0.1`）
- `--port`（既定: `7957`）
- `--log-dir`（既定: 未指定の場合は Windows の `%USERPROFILE%\AppData\LocalLow\VRChat\VRChat` を探索）
- `--log-file`（特定ログファイルを直接指定）
- `--replay <path>`（ログファイルを 0.01秒/行で再生して動作確認）

UI の表示項目（目安）:
- Source / Video ID / Position / Duration / Status（`/state` の値を表示）

`/state` の JSON（抜粋）:
```json
{
  "playing": true,
  "source": "youtube",
  "video_id": "XXXXXXXXXXX",
  "watch_url": "https://www.youtube.com/watch?v=XXXXXXXXXXX",
  "status": "playing",
  "estimated_position_sec": 12.34,
  "duration_sec": 180.0,
  "last_event": "Opening offset=0"
}
```

- `GET /state?fudge=秒` でポジションの補正量を調整（既定 `1.5` 秒）

---

## フォルダ構成
```
.
├─ src/vrc_log_video_sync/      # アプリ本体（エントリポイントは __main__.py）
├─ Dockerfile                   # マルチステージ。実行時は依存ゼロ
├─ compose.yaml                 # Docker Desktop (Windows) 用。USERPROFILE に自動フォールバック
├─ .env.example                 # 日本語の設定例（任意）
├─ pyproject.toml               # uv/スクリプト定義、dev 依存は dependency-groups に分離
├─ pyrightconfig.json           # 型チェック設定
├─ .dockerignore / .gitignore   # ビルド/リポジトリ不要物を除外
└─ uv.lock                      # 依存ロック
```

---

## 開発メモ
- Python: 3.13
- 実行: `uv run vrc-log-video-sync`
- Lint/Type Check（dev 依存インストール後）:
  - `uv run ruff check .`
  - `uv run pyright`

---

## セキュリティ/ネットワーク
- ローカルは `127.0.0.1` 既定で外部非公開。必要時のみ `--host 0.0.0.0` を指定
- Docker/Compose は既定で外部公開（ポート公開でアクセス可能）
- Docker Desktop の「File Sharing」でログ配置ドライブ（例: `C:`）の共有を許可してください

---

## トラブルシューティング
- 「ログが見つからない」: VRChat を起動しているか、`--log-dir`/`--log-file` を確認
- 「Compose のボリュームが失敗」: ドライブ共有の許可やパス表記（バックスラッシュ）を確認
- 「ポートが競合」: `.env` の `PORT` を変更し、`docker compose up -d` を再実行
- 「YouTube が再生されない」: 埋め込み不可の動画やネットワーク制限を確認

