# vrc-log-video-sync

VRChat のログ (`output_log_*.txt`) をリアルタイムで監視し、再生中の動画（主に YouTube）の状態をブラウザとコンソールに表示・同期するツールです。デフォルトでローカルホスト (`http://127.0.0.1:7957`) 上に簡易 UI を提供します。

---

## 使い方

### ダウンロード
Releasesからダウンロードしてください。  
https://github.com/attamari/vrc_log_video_sync/releases

### 配布バイナリから実行
1. フォルダー `vrc-log-video-sync` に移動します。
2. `vrc-log-video-sync.exe`を実行します。
3. ブラウザが自動で開きます。
4. VRChat上で動画が再生されると、ブラウザ上で動画が同期再生されます。

### Python から実行
```bash
uv run python -m vrc_log_video_sync
```
`uv` を使用しない場合は、仮想環境を有効化したうえで `python -m vrc_log_video_sync` を実行してください。


## トラブルシューティング
- 正しく再生されない
  - VRChat を一度起動してから、アプリケーションを起動してください。
  - ログの出力をオンにしてください。
    - 設定 > デバッグ情報 > ログの出力：完全
  - VRChatが複数起動していないか確認してください。
- ブラウザの動画が更新されない
  - アプリケーションが多重起動していないか確認してください。
- 再生が開始しない
  - 他のウェブサイトでの再生が無効化されている
    - 投稿者が再生を無効化しています。Youtubeで見るをクリックしてください。
  - 非対応の動画サービスである
    - Youtube以外に対応していません。


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

## ビルド手順
MSVCでスタンドアロンバイナリを作成します。

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


## ディレクトリ構成
- `src/vrc_log_video_sync/__main__.py` … メインスクリプト
- `scripts/build.ps1` … ビルドスクリプト
- `dist/vrc-log-video-sync/` … ビルド成果物
