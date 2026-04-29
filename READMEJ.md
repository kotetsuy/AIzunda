# AIzunda — ずんだもんと声で会話できる AI パイプライン

Ubuntu + AMD Ryzen AI Max+ 395 (ROCm) 上で、**音声 → STT → LLM → TTS → VRM リップシンク** を
一気通貫で動かすローカルスタック。ブラウザの 🎤 ボタンを押すとずんだもんが声で返します。

```
ブラウザ (three-vrm)
  └─ マイク録音 (MediaRecorder webm/opus)
         ↓ POST /voice_chat_speak_stream
    three-vrm サーバ (port 8000)
         ↓ POST /voice_chat_stream
       ttllm ブリッジ (port 8001)
         ├─ WhisperX-ROCm (STT, large-v3)
         └─ llama-server (Qwen3.6-35B-A3B, port 8080)
         ↓ SSE で token ストリーム
    three-vrm: 文境界で分割 → VOICEVOX (port 50021) → WS 配信
         ↓ WS (audio + visemes)
 ブラウザ: AudioContext 連続再生 + VRM リップシンク + 背景 + idle motion
```

## 構成

| パス | 役割 | ポート |
|---|---|---|
| `voicevox/` | VOICEVOX Engine (Docker, CPU 推論) | 50021 |
| `~/AIzunda/llama.cpp/build/bin/llama-server` | Qwen3.6 推論 | 8080 |
| `qwen3.6/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` | LLM モデル | — |
| `ttllm/` | FastAPI ブリッジ (WhisperX + llama.cpp) | 8001 |
| `three-vrm/` | aiohttp サーバ + VRM ビューア (HTML/three-vrm) | 8000 |
| `vtt/` | CLI PTT マイク (任意) | — |
| `images/` | VRM ビューア背景 (5 分ごとにローテーション) | — |
| `zundavrm/VRM/Zundamon_2025_VRM10A.vrm` | ずんだもん VRM 1.0 モデル | — |
| `whisperX-rocm/` | WhisperX の ROCm フォーク (venv は `~/AIzunda/whisperX-rocm/.venv`) | — |

### 前提

- **OS** : Ubuntu 24.04.4 LTS
- **GPU** : AMD Ryzen AI Max+ 395 / Radeon 8060S (gfx1151、48GB VRAM)
- **ROCm** : 7.2.1 (`/opt/rocm`)
- **Python** : 3.12.3
- **Docker** : 29.x (VOICEVOX 用)
- **ブラウザ** : Google Chrome (`AudioContext` を使うため Firefox でも可)
- **tmux / curl** : 起動スクリプトで使用

詳細なセットアップは各サブディレクトリの `READMEJ.md` を参照:
`ttllm/READMEJ.md` / `vtt/READMEJ.md` / `three-vrm/READMEJ.md` / `voicevox/READMEJ.md` /
`whisperX-rocm/READMEJ.md`。

## 一括起動 / 停止

```bash
~/AIzunda/start_all.sh   # 全段起動 + health check + WhisperX warmup + Chrome オープン
~/AIzunda/stop_all.sh    # tmux セッション + VOICEVOX を停止
~/AIzunda/stop_all.sh --keep-voicevox   # VOICEVOX コンテナは残す
```

`start_all.sh` は tmux セッション `aizunda` を作り、各サービスを別ウィンドウで走らせます。

| window | コマンド |
|---|---|
| 0 voicevox | `docker logs -f voicevox_engine` |
| 1 llama | `llama-server -m Qwen3.6... --port 8080 -ngl 99 -c 8192` |
| 2 ttllm | `ttllm/run.sh` (uvicorn) |
| 3 three-vrm | `python3 three-vrm/server.py` |
| 4 vtt | `vtt/run.sh --device USB` (CLI PTT, 任意) |

ログを見る: `tmux attach -t aizunda`  
全部落とす: `~/AIzunda/stop_all.sh`

起動順序は依存関係に合わせて直列化しており、各段で HTTP health check 待ちを入れています
(llama-server のモデルロードだけ最大 600 秒タイムアウト)。ttllm が上がった直後に
`/warmup` を叩いて WhisperX モデルをあらかじめロードするので、最初の発話が遅くなりません。

## ブラウザでの使い方

1. `start_all.sh` が自動で Chrome を開く (`http://localhost:8000/zundamon.html`)
2. 画面を一度クリックして AudioContext を有効化 (ブラウザの user-gesture 要件)
3. 右下の **🎤 ボタン**
   - **長押し (≥ 250ms)** : 押している間だけ録音、離すと送信
   - **短クリック** : 録音開始 → もう一度クリックで送信
4. ユーザー発話は薄青の字幕、ずんだもんの返答は白の字幕として表示

## レイテンシ最適化

短い発話 (「こんにちは」程度) で体感 1 秒前後、長文応答でも**初音 1 秒台**を目標にしています。

### 1. Qwen3 thinking モードを切る

既定で Qwen3 は返答前に `reasoning_content` (内部独白) を数百トークン吐き、
これで数秒の体感遅延が出ます。ttllm から llama-server に
`chat_template_kwargs: {"enable_thinking": false}` を渡して無効化しています
(`ttllm/server.py:_call_llama`)。この 1 行だけで LLM 段を 4〜8 秒短縮。

### 2. LLM → VOICEVOX のパイプライン化

- ttllm に `/voice_chat_stream` (SSE) を追加し、llama-server を `stream: true` で叩いて
  `{transcript}` → `{token}×N` → `{done}` の流れで返す。
- three-vrm の `/voice_chat_speak_stream` が SSE を消費。`[。！？\n]` で文分割、
  長文保険で 60 文字超は `[、]` でも切る。TTS は `asyncio.Queue` + consumer task で
  直列化 (WS 順序保証)、LLM デコードは並列継続。
- クライアントは `turn_start` で playhead をリセット、各 `speak` チャンクを
  `startAt = max(playheadTime, now)` でキュー末尾に連続再生。viseme は絶対時刻で
  スケジュールするので複数チャンクでも干渉しない。

結果 (実測、長文 8 文応答):

| 指標 | 改善前 (非streaming) | 改善後 (pipeline) |
|---|---|---|
| 初音までの時間 | **3.32 s** | **1.06 s** |
| 全体完了時間 | 3.32 s | 2.98 s |

### 3. 新ターン開始時に前の発話を即停止

マイクを押した時点で、クライアントは現在スケジュール済みの全 `AudioBufferSourceNode` を
`stop(0)` → viseme キューも消す、という処理を入れています (`stopAllPlayback`)。
サーバの `turn_start` 到着を待たないので体感が即応。

## VRM ビューアの演出

### 背景ランダムローテーション

- 画像は `~/AIzunda/images/*.{jpg,png,webp}` を自動検出
- `GET /images_list` でファイル一覧、`GET /images/<name>` で配信
- ページ読み込み時に 1 枚選択、**5 分ごと**にランダムで別の画像に切替 (`zundamon.html`)
- 画像は同梱されていません。追加する場合はディレクトリに放り込むだけ (サーバ再起動不要)

### Idle モーション

T-pose 棒立ちを避けるため、毎フレーム微小な回転を加えています
(`zundamon.html:applyIdlePose`)。

| 部位 | 周波数 | 振幅 |
|---|---|---|
| spine / chest (X 軸、呼吸) | 0.25 Hz | ±0.7° |
| spine / chest (Z 軸、左右揺れ) | 0.13 Hz (位相違い) | ±1.1° |
| head (X 軸) | 0.10 Hz | ±0.9° |
| head (Y 軸) | 0.08 Hz | ±1.7° |

`vrm.update(delta)` の前にポーズを設定しているので、VRM の spring bones (髪・スカート等)
が自然に二次追従します。

### 両手を下ろす

VRM のデフォルトは T-pose なので、ロード直後に `applyRestPose()` で
両腕を自然立ちの位置に落とし、肘も約 14° 曲げています (`zundamon.html`)。

## 主要エンドポイント

### ttllm (port 8001)

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/health` | 自身 + llama-server 到達性 |
| POST | `/warmup` | WhisperX モデル先読み |
| POST | `/transcribe` | 音声 → テキスト |
| POST | `/chat` | テキスト → LLM 応答 (非streaming) |
| POST | `/voice_chat` | 音声 → 応答 (非streaming) |
| POST | `/voice_chat_stream` | 音声 → SSE (transcript + token + done) **new** |

### three-vrm (port 8000)

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/zundamon.html` | ビューア |
| GET | `/ws` | WebSocket (turn_start / speak / turn_end / transcript / error) |
| POST | `/speak` | テキスト指定で発話 |
| POST | `/voice_chat_speak` | 音声 → ワンショット応答 (非streaming) |
| POST | `/voice_chat_speak_stream` | 音声 → パイプライン応答 **new** |
| GET | `/images_list` | 背景画像一覧 |
| GET | `/images/{name}` | 背景画像配信 |
| GET | `/vrm/{name}` | VRM ファイル配信 |
| GET | `/status` | クライアント数 |

## 既知の制約

- **WhisperX は 60 秒超で GPU memory fault** (ROCm 7.x + PyTorch nightly の既知問題)。
  vtt は VAD で 55 秒に強制カットして回避しています。ブラウザ側の録音も長尺は避けてください。
- **無音発話で以前 500 エラー** が出ていましたが、Silero VAD が "No active speech" を
  返したときの WhisperX IndexError を `_transcribe_path` で捕捉して空文字に落とすように
  修正済 (`ttllm/server.py`)。
- **VOICEVOX は CPU 推論**。ROCm との VRAM 競合を避けるための選択で、
  短文なら十分リアルタイム。長文では合成が律速になる可能性あり。
- **Chrome の AudioContext** は初回クリックが必須 (user-gesture 要件)。
- **Qwen3 の thinking** は ttllm 経由では常に OFF ですが、llama-server を直叩きする場合は
  `chat_template_kwargs` を自分で付与する必要があります。

## パスについて

全 shell script / Python のハードコードパスは `$USER` / `os.path.expanduser("~/...")`
に置換済で、`/home/<someone>` の決め打ちは残っていません。他ユーザーで動かす場合でも、
`~/AIzunda/`, `~/llama.cpp/`, `~/venv/whisperx-rocm/` のディレクトリ構造さえ揃えれば
動きます。

## トラブルシュート

| 現象 | 対処 |
|---|---|
| 🎤 を押しても無音 | 画面をクリックして AudioContext を有効化。ブラウザの mic 権限も確認 |
| ずんだもんが喋らない / 500 エラー | `tmux attach -t aizunda` で ttllm のログ確認。`curl :8001/health` で llama 到達性もチェック |
| 初回発話が遅い | `curl -X POST :8001/warmup` で WhisperX 先読み |
| 腕の向きがおかしい (VRM 差し替え時) | `zundamon.html:applyRestPose` の `rotation.z` 符号を反転 |
| 背景が切り替わらない | DevTools console で `/images_list` のレスポンスを確認。画像を置いたらブラウザリロード |
| VRM が読めない | `server.py` の `VRM_DIR` と実ファイルパスを確認。ファイル名は `zundamon.html` の `VRM_URL` に一致させる |
| 全部止めたい | `~/AIzunda/stop_all.sh` |

## まとめ

ローカル完結で、クラウド API に依存しない「声で会話できるずんだもん」を、
AMD Ryzen AI Max+ 395 + ROCm のワンマシン上で動かすことをゴールにしています。
Qwen3.6-35B-A3B の thinking 抑制と LLM→TTS のパイプライン化で、
初音まで約 1 秒、違和感のない待機モーションと背景演出を最小コードで付けています。

拡張の余地は以下あたりです。

- 会話履歴の保持 (現在は毎ターンステートレス、`history` パラメタで渡すだけ)
- VRMA 形式の idle アニメ読み込み (現在はプロシージャル)
- VOICEVOX を GPU ビルドに差し替え (長文応答の合成を高速化)
- smaller STT model への切替 (medium で 200〜300 ms 短縮可能)
- LLM ストリーミング中の手振りジェスチャ連動
