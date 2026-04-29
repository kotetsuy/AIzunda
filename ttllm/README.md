# ttllm — WhisperX ↔ llama.cpp bridge

A minimal FastAPI bridge that wires WhisperX (speech recognition) to llama.cpp
(`llama-server`). POST audio in, get a transcript and an LLM reply back in one
call. Designed to be hit directly from AIzunda's frontends such as
`talkinghead` / `zundavrm`.

## Layout

```
ttllm/
├── server.py    # FastAPI app
├── install.sh   # adds extra deps to the whisperX-rocm venv
├── run.sh       # sets ROCm env vars and launches uvicorn
└── README.md    # this file
```

It shares the venv where WhisperX-ROCm is already installed
(`~/AIzunda/whisperx-rocm/.venv`), so torch-ROCm / ctranslate2-rocm are not
duplicated.

## Prerequisites

- WhisperX-ROCm (whisperx / torch 2.9+rocm / ctranslate2 / faster-whisper /
  pyannote.audio) installed in `~/AIzunda/whisperx-rocm/.venv`
- `~/AIzunda/llama.cpp/build/bin/llama-server` already built
- Qwen3.6 model at `~/AIzunda/qwen3.6/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf`

## Setup

```bash
cd ~/AIzunda/ttllm
./install.sh
```

This adds `fastapi` / `uvicorn` / `httpx` / `python-multipart` / `pydantic`
to the WhisperX venv.

## Launch

**1. Start llama-server** (in another terminal)

```bash
cd ~/AIzunda/llama.cpp/build/bin
./llama-server \
    -m ~/AIzunda/qwen3.6/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf \
    --host 127.0.0.1 --port 8080 \
    -ngl 99 -c 8192
```

**2. Start the bridge**

```bash
cd ~/AIzunda/ttllm
./run.sh
```

Listens on `http://0.0.0.0:8001` by default. Swagger UI is at
`http://localhost:8001/docs`.

## Endpoints

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET    | `/health`     | Reachability of self / WhisperX / llama-server |
| POST   | `/warmup`     | Preload the WhisperX model to remove first-call latency |
| POST   | `/transcribe` | Audio → text (no LLM) |
| POST   | `/chat`       | Text → LLM reply |
| POST   | `/voice_chat` | Audio → transcript + LLM reply, in one call |

### `/voice_chat` (multipart/form-data)

| Field | Type | Default | Description |
| ----- | ---- | ------- | ----------- |
| `audio`       | file            | —       | wav / mp3 / m4a etc. |
| `system`      | str             | Zundamon persona | Override system prompt |
| `history`     | str (JSON list) | `[]`    | `[{"role":"user","content":"..."}]` |
| `temperature` | float           | `0.7`   | |
| `max_tokens`  | int             | `512`   | |

Response:

```json
{ "transcript": "こんにちは", "reply": "こんにちはなのだ！" }
```

### `/chat` (application/json)

```json
{
  "text": "自己紹介して",
  "history": [],
  "system": null,
  "temperature": 0.7,
  "max_tokens": 512
}
```

### Examples

```bash
# From an audio file all the way to a reply
curl -X POST http://localhost:8001/voice_chat \
    -F "audio=@sample.wav"

# Text-only LLM call
curl -X POST http://localhost:8001/chat \
    -H 'Content-Type: application/json' \
    -d '{"text":"ずんだ餅について教えてなのだ"}'

# Preload the model (kills first-call latency)
curl -X POST http://localhost:8001/warmup
```

## Environment variables

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `WHISPER_MODEL`        | `large-v3`              | WhisperX model name |
| `WHISPER_LANGUAGE`     | `ja`                    | Recognition language |
| `WHISPER_COMPUTE_TYPE` | `float16`               | `float16` / `int8_float16` etc. |
| `WHISPER_DEVICE`       | `cuda`                  | GPU is used through ROCm's HIP layer |
| `WHISPER_BATCH_SIZE`   | `8`                     | |
| `WHISPER_VAD_METHOD`   | `silero`                | `silero` / `pyannote` |
| `LLAMA_SERVER_URL`     | `http://localhost:8080` | URL of llama-server |
| `LLAMA_TIMEOUT`        | `120`                   | seconds |
| `SYSTEM_PROMPT`        | Zundamon persona        | Default system prompt |
| `BRIDGE_HOST`          | `0.0.0.0`               | |
| `BRIDGE_PORT`          | `8001`                  | |
| `WHISPERX_VENV`        | `~/venv/whisperx-rocm`  | Path of the shared venv (the one with whisperx / torch-ROCm / ctranslate2) |

## Calling from a frontend

The server starts with permissive CORS, so browsers (e.g. `talkinghead` /
`zundavrm`) can `fetch` it directly:

```javascript
const fd = new FormData();
fd.append("audio", blob, "utterance.wav");
const res = await fetch("http://localhost:8001/voice_chat", {
  method: "POST",
  body: fd,
});
const { transcript, reply } = await res.json();
```

## Caveats

- Audio longer than 60 s can trigger ROCm memory faults (see `~/CLAUDE.md`).
  Chunk on the client side for long inputs.
- `/chat` and `/voice_chat` are stateless. Keep history on the caller side
  and pass it via the `history` field.
- Bridging to TTS (VOICEVOX) is out of scope here. The receiver of `reply`
  is responsible for synthesis.
