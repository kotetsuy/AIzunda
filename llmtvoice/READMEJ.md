# AIずんだもん - 音声対話パイプライン総合ドキュメント

マイク入力から、VRM モデル（ずんだもん）がブラウザ上で口パクしながら返答するまでを、
ROCm 搭載 AMD GPU 上で完結させる音声対話システム。各コンポーネントは疎結合な
HTTP サービスとして独立しており、必要な部分だけ差し替えが可能。

本ドキュメントは各コンポーネント（`whisperX-rocm` / `ttllm` / `voicevox` / `three-vrm`）
の個別 README をまとめたパイプライン視点の最終版です。

---

## 全体像

```
┌──────────────────────┐
│ ブラウザ（マイク入力）      │  http://localhost:8000/zundamon.html
│   MediaRecorder → webm  │
└──────────┬────────────┘
           │ multipart POST /voice_chat_speak
           ▼
┌──────────────────────┐
│ three-vrm サーバー (port 8000)│  aiohttp
│ - /voice_chat_speak       │───┐
│ - /speak                  │   │
│ - /ws (WebSocket)         │   │
│ - /vrm/*.vrm              │   │
└──────────┬────────────┘   │
           │ ttllm /voice_chat │  (音声 → STT → LLM)
           ▼                   │
┌──────────────────────┐   │
│ ttllm ブリッジ (port 8001)  │   │  FastAPI
│ - /voice_chat             │   │
│ - /chat                   │   │
│ - /transcribe             │   │
└──────┬───────┬───────┘   │
       │       │               │
       ▼       ▼               │
┌────────┐ ┌──────────────┐ │
│WhisperX│ │ llama-server │ │
│  ROCm  │ │  (llama.cpp) │ │
│        │ │ Qwen3.6-35B  │ │
└────────┘ └──────────────┘ │
                                │
           ┌────────────────┘
           │ reply テキスト
           ▼
┌──────────────────────┐
│ VOICEVOX Engine (50021) │  Docker / CPU 推論
│ /audio_query → /synthesis │
└──────────┬────────────┘
           │ WAV + accent_phrases
           ▼
┌──────────────────────┐
│ three-vrm: visemes 変換    │
│ → WS ブロードキャスト     │
└──────────┬────────────┘
           ▼
┌──────────────────────┐
│ ブラウザ: 音声再生 + 口パク   │
│  @pixiv/three-vrm 1.0 表情 │
│  (aa / ih / ou / ee / oh / nn)│
└──────────────────────┘
```

---

## ディレクトリ構成

```
~/AIzunda/
├── whisperX-rocm/       # STT（WhisperX + CTranslate2-ROCm）
├── ctranslate2-rocm/    # ROCm/HIP 対応 CTranslate2（ソースビルド）
├── llama.cpp/           # LLM 推論エンジン（llama-server）
├── qwen3.6/             # GGUF モデル格納
├── ttllm/               # WhisperX ↔ llama.cpp ブリッジ（FastAPI）
├── voicevox/            # VOICEVOX Docker 起動定義・テストスクリプト
├── three-vrm/           # VRM ビューア兼 VOICEVOX 中継（aiohttp）
│   └── TalkingHead/     # ブラウザ側フロント（zundamon.html）
├── zundavrm/            # ずんだもん VRM モデル一式
└── llmtvoice/           # 本 README（パイプライン総合ドキュメント）
```

---

## 必要環境

| 項目       | 要件 |
| ---------- | ---- |
| OS         | Ubuntu 24.04 LTS |
| GPU        | AMD Ryzen AI Max+ 395 / Radeon 8060S (gfx1151, 48GB VRAM) |
| ROCm       | 7.2.0 (`/opt/rocm`) |
| Python     | 3.12.3 |
| Docker     | 29.x（VOICEVOX 用） |
| Node       | 不要（ブラウザは CDN ではなくローカル配信の three.js を利用） |

ROCm 環境変数は各 `run.sh` 内で設定済み。手動起動時も必要:
```bash
export HSA_OVERRIDE_GFX_VERSION=11.5.1
export ROCM_PATH=/opt/rocm
export HIP_VISIBLE_DEVICES=0
export LD_LIBRARY_PATH=/usr/local/lib:/opt/rocm/lib:/opt/rocm/lib/llvm/lib:$LD_LIBRARY_PATH
```

---

## セットアップ（初回のみ）

### 1. CTranslate2-ROCm をソースビルド
```bash
cd ~/AIzunda/ctranslate2-rocm/build
cmake .. -DWITH_HIP=ON -DWITH_MKL=OFF -DWITH_OPENBLAS=ON \
  -DCMAKE_HIP_ARCHITECTURES=gfx1151 -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_HIP_COMPILER=/opt/rocm/lib/llvm/bin/clang++ \
  -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/clang++ \
  -DCMAKE_C_COMPILER=/opt/rocm/lib/llvm/bin/clang \
  -DCMAKE_PREFIX_PATH=/opt/rocm -DBUILD_CLI=OFF
make -j$(nproc) && sudo make install
```

### 2. WhisperX venv を作成
```bash
cd ~/AIzunda/whisperX-rocm
uv venv && uv pip install -e .

# ROCm 版 ctranslate2 Python バインディングを再インストール
rm -rf .venv/lib/python3.12/site-packages/ctranslate2*
export CTRANSLATE2_ROOT=/usr/local
uv pip install --reinstall pybind11 ~/AIzunda/ctranslate2-rocm/python
```

### 3. llama.cpp をビルド
llama.cpp 本体側の `CLAUDE.md` / `AGENTS.md` に従う。ROCm (HIP) 対応で
`llama-server` をビルドしておく。

### 4. ttllm ブリッジの依存を whisperX venv に追加
```bash
cd ~/AIzunda/ttllm && ./install.sh
```

### 5. VOICEVOX Docker を取得 & 起動
```bash
docker pull voicevox/voicevox_engine:cpu-ubuntu20.04-latest
docker run -d --name voicevox_engine --restart unless-stopped \
  -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu20.04-latest
```

### 6. VRM モデルを配置
`~/AIzunda/zundavrm/VRM/Zundamon_2025_VRM10A.vrm`

（`three-vrm/server.py` の `VRM_DIR` / ファイル名は必要に応じて書き換え）

---

## 起動手順（毎回）

4 プロセスを順に立ち上げる。全て永続化したい場合は systemd / tmux を推奨。

### ① VOICEVOX（Docker）
```bash
docker start voicevox_engine
# 確認
curl -s http://localhost:50021/version
```

### ② llama-server（LLM）
```bash
cd ~/AIzunda/llama.cpp/build/bin
./llama-server \
    -m ~/AIzunda/qwen3.6/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf \
    --host 127.0.0.1 --port 8080 \
    -ngl 99 -c 8192
```

### ③ ttllm ブリッジ（WhisperX + LLM）
```bash
cd ~/AIzunda/ttllm && ./run.sh
# http://localhost:8001/docs で Swagger UI
curl -X POST http://localhost:8001/warmup  # WhisperX 先読み推奨
```

### ④ three-vrm（VRM ビューア兼 VOICEVOX 中継）
```bash
cd ~/AIzunda/three-vrm && python3 server.py
```

ブラウザで `http://localhost:8000/zundamon.html` を開き、
画面を **一度クリック**（AudioContext とマイク権限の解放）→ 右下 🎤 で発話。

---

## ポート / エンドポイント一覧

| サービス      | ポート | 主要エンドポイント |
| ------------- | ------ | ------------------ |
| VOICEVOX      | 50021  | `/audio_query`, `/synthesis` |
| llama-server  | 8080   | `/v1/chat/completions` (OpenAI 互換) |
| ttllm         | 8001   | `/voice_chat`, `/chat`, `/transcribe`, `/warmup`, `/health` |
| three-vrm     | 8000   | `/zundamon.html`, `/voice_chat_speak`, `/speak`, `/ws`, `/vrm/*` |

### `/voice_chat_speak`（ワンショット API）

multipart/form-data:

| フィールド     | 型              | 既定値 | 説明 |
| -------------- | --------------- | ------ | ---- |
| `audio`        | file            | —      | webm / wav / mp3 / m4a 等 |
| `speaker_id`   | int             | `3`    | VOICEVOX スピーカー ID（3=ノーマル ずんだもん）|
| `system`       | str             | ttllm 既定 | LLM system prompt 上書き |
| `history`      | str (JSON list) | `[]`   | 会話履歴 |
| `temperature`  | float           | `0.7`  | LLM |
| `max_tokens`   | int             | `512`  | LLM |

レスポンス:
```json
{"ok": true, "transcript": "...", "reply": "...", "visemes": 42, "clients": 1}
```

合成音声＋口パクデータは WebSocket 経由で接続中の全クライアントに
ブロードキャストされる（レスポンス本体には含まれない）。

---

## ブラウザ側 UI

`zundamon.html` に組み込み済み:

- **右下 🎤 ボタン**
  - **長押し（≥ 250ms）**: 押している間だけ録音、離すと送信（PTT）
  - **短クリック**: 録音開始 → もう一度クリックで送信（トグル）
- **字幕**
  - 薄青: ユーザー発話の文字起こし
  - 白:   ずんだもんの応答テキスト
- **リップシンク**
  - VRM1.0 標準表情 `aa / ih / ou / ee / oh / nn` を
    `audioCtx.currentTime` ベースでスケジュール

初回は **画面を一度クリック** して AudioContext とマイク権限を解放。

---

## ずんだもん スピーカー ID

| スタイル   | ID |
| ---------- | -- |
| ノーマル   | 3  |
| あまあま   | 1  |
| ツンツン   | 7  |
| セクシー   | 5  |
| ささやき   | 22 |
| ヒソヒソ   | 38 |
| ヘロヘロ   | 75 |
| なみだめ   | 76 |

`zundamon.html` 冒頭の `SPEAKER_ID` で既定値を変更可。

---

## 動作確認

```bash
# 各サービスの疎通
curl -s http://localhost:50021/version
curl -s http://localhost:8080/health
curl -s http://localhost:8001/health

# three-vrm 経由でテキスト → VOICEVOX → VRM 口パク
curl -X POST http://localhost:8000/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"こんにちはなのだ","speaker_id":3}'

# ttllm だけでテキスト対話（VRM なし）
curl -X POST http://localhost:8001/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"自己紹介してなのだ"}'

# 音声ファイル → 文字起こし + LLM 応答 + 合成 + ブラウザ口パク
curl -X POST http://localhost:8000/voice_chat_speak \
  -F "audio=@sample.wav" -F "speaker_id=3"
```

---

## 既知の問題・注意点

### 1. WhisperX の 60 秒超メモリフォールト
ROCm 7.x + PyTorch nightly の組み合わせで発生。
```
Memory access fault by GPU node-1... Reason: Page not present or supervisor privilege.
```
対策: クライアント側で 60 秒未満にチャンク分割、または `clip_timestamps=[0, 60]` を
faster-whisper 直叩きで利用。ブラウザ側は `MediaRecorder` を短めに停止する運用で回避。

### 2. three.js r170 以降の 2 ファイル構成
`three.module.js` + `three.core.js` の両方を配置しないと、
Chrome が `Failed to fetch dynamically imported module` という紛らわしい
エラーを出す（実体は依存解決失敗）。両方 `libs/three/` に必須。

### 3. ステートレス
`/chat` `/voice_chat` `/voice_chat_speak` いずれも会話履歴を保持しない。
連続会話を実現するには、呼び出し側で `history` フィールドに過去のやり取りを
JSON list で渡す。

### 4. AudioContext / マイク権限
ブラウザの user-gesture ポリシーにより、画面の最初のクリックが必要。
`zundamon.html` は起動直後に「クリックして音声を有効化」のオーバーレイを
表示し、クリックで AudioContext 解放 + 🎤 ボタン活性化を同時に行う。

### 5. VOICEVOX は CPU 推論
ROCm ランタイムとの干渉を避けるため CPU コンテナを採用。長文でレイテンシが
気になる場合は `speed_scale` や事前分割で対応。

---

## 今後の拡張候補

- **会話履歴の永続化**: three-vrm 側でセッションストアを持ち、ブラウザと協調管理
- **ストリーミング応答**: llama.cpp の SSE を活かし、
  文節単位で VOICEVOX 合成 → 早期発話開始（初声レイテンシ短縮）
- **VAD による自動停止**: ブラウザ側で webrtcvad / silero-wasm を使い、
  🎤 長押しを不要にする
- **マルチキャラ**: `speaker_id` と VRM ファイルを紐付け、キャラ切替 UI を追加
- **感情表情**: LLM プロンプトに `<emotion>...</emotion>` を出力させ、
  VRM1.0 の `happy / sad / angry` 表情にマッピング

---

## 参考・ライセンス

- WhisperX: https://github.com/m-bain/whisperX (BSD-4-Clause)
- CTranslate2: https://github.com/OpenNMT/CTranslate2 (MIT)
- llama.cpp: https://github.com/ggerganov/llama.cpp (MIT)
- VOICEVOX: https://voicevox.hiroshiba.jp/ （利用規約・キャラクター個別ライセンス要確認）
- ずんだもん VRM: `~/AIzunda/zundavrm/Zundamon_vn3license_*.pdf` 参照
- three-vrm: https://github.com/pixiv/three-vrm (MIT)

各コンポーネントの詳細は以下を参照:
- `~/AIzunda/whisperX-rocm/README.md`
- `~/AIzunda/ttllm/READMEJ.md`
- `~/AIzunda/voicevox/READMEJ.md`
- `~/AIzunda/three-vrm/READMEJ.md`
- `~/CLAUDE.md`（ROCm 環境メモ）
