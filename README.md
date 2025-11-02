# vrc-log-video-sync

VRChat のログ (`output_log_*.txt`) をリアルタイムで監視し、再生中の動画（主に YouTube）の状態をブラウザとコンソールに表示・同期するツールです。デフォルトでローカルホスト (`http://127.0.0.1:7957`) 上に簡易 UI を提供します。

---

## 使い方

### 配布バイナリから実行
1. フォルダー `dist\vrc-log-video-sync` に移動します。
2. 以下のいずれかで実行します。
   - 自動で最新ログを追尾: `vrc-log-video-sync.exe`
   - ログディレクトリを指定: `vrc-log-video-sync.exe --log-dir "%USERPROFILE%\AppData\LocalLow\VRChat\VRChat"`
3. ブラウザで `http://127.0.0.1:7957/client` を開きます。

### Python から実行
```bash
uv run python -m vrc_log_video_sync --log-dir "%USERPROFILE%\AppData\LocalLow\VRChat\VRChat"
```
`uv` を使用しない場合は、仮想環境を有効化したうえで `python -m vrc_log_video_sync` を実行してください。

---

## 主なオプション

`python -m vrc_log_video_sync --help`

- `--host` (既定: `127.0.0.1`)
- `--port` (既定: `7957`)
- `--log-dir` / `--log-file` : 追尾するログディレクトリまたはファイルを指定
- `--replay <path>` : ログファイルを 0.01 秒間隔で再生
- `--no-browser` : UI を自動で開かない
- `--no-tui` : コンソール TUI を表示しない

---

## API エンドポイント
- UI: `GET /client`
- 状態取得: `GET /state?fudge=<秒>` （既定 1.5 秒、推定再生位置の補正に使用）

---

## ビルド手順
MSVC onedir でスタンドアロンバイナリを作成します。

### 前提
- Windows 10/11 (x64)
- Visual Studio 2022 Build Tools（C++ ツール + Windows SDK）
- [uv](https://astral.sh/uv)

### PowerShell
```powershell
pwsh -File scripts/build.ps1 -UseIcon -SelfSign
```

### 出力
`dist\vrc-log-video-sync\` に以下が生成されます。
- `vrc-log-video-sync.exe`
- `python312.dll`, `vcruntime140.dll`, `vcruntime140_1.dll`
- 必要に応じて `_socket.pyd`, `select.pyd`, `unicodedata.pyd`, `_wmi.pyd`

---

## トラブルシューティング
- ログが追尾されない場合は VRChat を一度起動し、`--log-dir` または `--log-file` を指定してください。
- ブラウザ UI の状態が更新されない場合は `/state` の `source`, `video_id`, `status` を確認してください。
- YouTube IFrame の再生が開始しない場合は、ブラウザのネットワーク設定や拡張機能を確認してください。

---

## ディレクトリ構成
- `src/vrc_log_video_sync/__main__.py` … メインスクリプト
- `scripts/build.ps1` … ビルドスクリプト
- `scripts/convert_icon.py` … `icon.png` を `icon.ico` へ変換
- `dist/vrc-log-video-sync/` … ビルド成果物
