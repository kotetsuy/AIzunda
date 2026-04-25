# ttllm — WhisperX ↔ llama.cpp ブリッジ

WhisperX（音声認識）と llama.cpp（`llama-server`）を繋ぐ、最小構成の FastAPI ブリッジサービスです。音声を投げると文字起こし → LLM 応答までをひとまとめに返します。`talkinghead` / `zundavrm` など AIzunda パイプラインのフロントから直接叩くことを想定しています。

## 構成

```
ttllm/
├── server.py    # FastAPI アプリ本体
├── install.sh   # whisperX-rocm の venv に追加依存をインストール
├── run.sh       # ROCm 環境変数を設定して uvicorn を起動
└── READMEJ.md   # このファイル
```

WhisperX-ROCm がインストール済みの venv（`~/AIzunda/whisperx-rocm/.venv`）を共有して動かすので、torch-ROCm / ctranslate2-rocm を二重に入れる必要はありません。

## 前提

- `~/AIzunda/whisperx-rocm/.venv` に WhisperX-ROCm 一式（whisperx / torch 2.9+rocm / ctranslate2 / faster-whisper / pyannote.audio）が入っていること
- `~/AIzunda/llama.cpp/build/bin/llama-server` がビルド済みであること
- Qwen3.6 モデル: `~/AIzunda/qwen3.6/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf`

## セットアップ

```bash
cd ~/AIzunda/ttllm
./install.sh
```

`fastapi` / `uvicorn` / `httpx` / `python-multipart` / `pydantic` を whisperX の venv に追加します。

## 起動

**1. llama-server を立ち上げる**（別ターミナル）

```bash
cd ~/AIzunda/llama.cpp/build/bin
./llama-server \
    -m ~/AIzunda/qwen3.6/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf \
    --host 127.0.0.1 --port 8080 \
    -ngl 99 -c 8192
```

**2. ブリッジを立ち上げる**

```bash
cd ~/AIzunda/ttllm
./run.sh
```

デフォルトで `http://0.0.0.0:8001` で待ち受けます。`http://localhost:8001/docs` で Swagger UI が見られます。

## エンドポイント

| メソッド | パス            | 用途 |
| -------- | --------------- | ---- |
| GET      | `/health`       | 本体 / WhisperX / llama-server の到達状況 |
| POST     | `/warmup`       | WhisperX モデルを先読みして初回遅延を消す |
| POST     | `/transcribe`   | 音声 → テキスト（LLM を経由しない） |
| POST     | `/chat`         | テキスト → LLM 応答 |
| POST     | `/voice_chat`   | 音声 → 文字起こし + LLM 応答をまとめて返す |

### `/voice_chat`（multipart/form-data）

| フィールド    | 型                       | 既定値  | 説明 |
| ------------- | ------------------------ | ------- | ---- |
| `audio`       | file                     | —       | wav / mp3 / m4a 等 |
| `system`      | str                      | ずんだもん persona | システムプロンプト上書き |
| `history`     | str (JSON list)          | `[]`    | `[{"role":"user","content":"..."}]` 形式 |
| `temperature` | float                    | `0.7`   | |
| `max_tokens`  | int                      | `512`   | |

レスポンス:

```json
{ "transcript": "こんにちは", "reply": "こんにちはなのだ！" }
```

### `/chat`（application/json）

```json
{
  "text": "自己紹介して",
  "history": [],
  "system": null,
  "temperature": 0.7,
  "max_tokens": 512
}
```

### 使用例

```bash
# 音声ファイルから一気に応答まで
curl -X POST http://localhost:8001/voice_chat \
    -F "audio=@sample.wav"

# テキストだけで LLM に聞く
curl -X POST http://localhost:8001/chat \
    -H 'Content-Type: application/json' \
    -d '{"text":"ずんだ餅について教えてなのだ"}'

# モデル先読み（初回の体感遅延を消す）
curl -X POST http://localhost:8001/warmup
```

## 環境変数

| 変数                    | 既定値                         | 説明 |
| ----------------------- | ------------------------------ | ---- |
| `WHISPER_MODEL`         | `large-v3`                     | WhisperX モデル名 |
| `WHISPER_LANGUAGE`      | `ja`                           | 認識言語 |
| `WHISPER_COMPUTE_TYPE`  | `float16`                      | `float16` / `int8_float16` など |
| `WHISPER_DEVICE`        | `cuda`                         | ROCm の HIP レイヤー経由で GPU が使われる |
| `WHISPER_BATCH_SIZE`    | `8`                            | |
| `WHISPER_VAD_METHOD`    | `silero`                       | `silero` / `pyannote` |
| `LLAMA_SERVER_URL`      | `http://localhost:8080`        | llama-server の URL |
| `LLAMA_TIMEOUT`         | `120`                          | 秒 |
| `SYSTEM_PROMPT`         | ずんだもん persona             | 既定システムプロンプト |
| `BRIDGE_HOST`           | `0.0.0.0`                      | |
| `BRIDGE_PORT`           | `8001`                         | |
| `WHISPERX_VENV`         | `~/venv/whisperx-rocm`         | 共有する venv のパス（whisperx / torch-ROCm / ctranslate2 が入っている側） |

## フロントからの呼び出し

CORS は全許可で立ち上がるので、`talkinghead` / `zundavrm` などのブラウザ側から直接 `fetch` できます。例:

```javascript
const fd = new FormData();
fd.append("audio", blob, "utterance.wav");
const res = await fetch("http://localhost:8001/voice_chat", {
  method: "POST",
  body: fd,
});
const { transcript, reply } = await res.json();
```

## 既知の注意点

- 60 秒を超える音声は ROCm 側の既知問題でメモリフォールトを起こすことがあります（`~/CLAUDE.md` 参照）。長尺はクライアント側で分割してください。
- `/chat` と `/voice_chat` はステートレスです。会話履歴は呼び出し側で保持し、`history` に詰めて渡してください。
- TTS（VOICEVOX）への橋渡しはこのブリッジの責務外です。`reply` を受け取った側で別途合成してください。
