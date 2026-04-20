# AIzunda — end-to-end voice-chat pipeline reference

A voice-chat system that runs entirely on an ROCm-capable AMD GPU: mic input in,
a VRM Zundamon lip-syncing a reply in the browser out. Every component is a
loosely-coupled HTTP service you can swap independently.

This document is the pipeline-level consolidation of each component README
(`whisperX-rocm` / `ttllm` / `voicevox` / `three-vrm`).

---

## Big picture

```
┌──────────────────────┐
│ Browser (mic input)      │  http://localhost:8000/zundamon.html
│   MediaRecorder → webm  │
└──────────┬────────────┘
           │ multipart POST /voice_chat_speak
           ▼
┌──────────────────────┐
│ three-vrm server (port 8000)│  aiohttp
│ - /voice_chat_speak       │───┐
│ - /speak                  │   │
│ - /ws (WebSocket)         │   │
│ - /vrm/*.vrm              │   │
└──────────┬────────────┘   │
           │ ttllm /voice_chat │  (audio → STT → LLM)
           ▼                   │
┌──────────────────────┐   │
│ ttllm bridge (port 8001)    │   │  FastAPI
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
           │ reply text
           ▼
┌──────────────────────┐
│ VOICEVOX Engine (50021) │  Docker / CPU inference
│ /audio_query → /synthesis │
└──────────┬────────────┘
           │ WAV + accent_phrases
           ▼
┌──────────────────────┐
│ three-vrm: viseme conversion │
│ → WS broadcast             │
└──────────┬────────────┘
           ▼
┌──────────────────────┐
│ Browser: playback + lip-sync │
│  @pixiv/three-vrm 1.0 expressions │
│  (aa / ih / ou / ee / oh / nn)│
└──────────────────────┘
```

---

## Directory layout

```
~/AIzunda/
├── whisperX-rocm/       # STT (WhisperX + CTranslate2-ROCm)
├── ctranslate2-rocm/    # ROCm/HIP CTranslate2 (built from source)
├── llama.cpp/           # LLM inference engine (llama-server)
├── qwen3.6/             # GGUF model storage
├── ttllm/               # WhisperX ↔ llama.cpp bridge (FastAPI)
├── voicevox/            # VOICEVOX Docker launch + test scripts
├── three-vrm/           # VRM viewer + VOICEVOX relay (aiohttp)
│   └── TalkingHead/     # browser front-end (zundamon.html)
├── zundavrm/            # Zundamon VRM model bundle
└── llmtvoice/           # this README (pipeline reference)
```

---

## Required environment

| Item       | Requirement |
|------------|-------------|
| OS         | Ubuntu 24.04 LTS |
| GPU        | AMD Ryzen AI Max+ 395 / Radeon 8060S (gfx1151, 48 GB VRAM) |
| ROCm       | 7.2.0 (`/opt/rocm`) |
| Python     | 3.12.3 |
| Docker     | 29.x (for VOICEVOX) |
| Node       | not needed (browser loads three.js locally, not from CDN) |

ROCm env vars are already set inside each `run.sh`. For manual launches:
```bash
export HSA_OVERRIDE_GFX_VERSION=11.5.1
export ROCM_PATH=/opt/rocm
export HIP_VISIBLE_DEVICES=0
export LD_LIBRARY_PATH=/usr/local/lib:/opt/rocm/lib:/opt/rocm/lib/llvm/lib:$LD_LIBRARY_PATH
```

---

## First-time setup

### 1. Build CTranslate2-ROCm from source
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

### 2. Create the WhisperX venv
```bash
cd ~/AIzunda/whisperX-rocm
uv venv && uv pip install -e .

# Reinstall the ROCm ctranslate2 Python bindings
rm -rf .venv/lib/python3.12/site-packages/ctranslate2*
export CTRANSLATE2_ROOT=/usr/local
uv pip install --reinstall pybind11 ~/AIzunda/ctranslate2-rocm/python
```

### 3. Build llama.cpp
Follow llama.cpp's own `CLAUDE.md` / `AGENTS.md`. Build `llama-server` with
ROCm (HIP) support.

### 4. Add ttllm bridge deps to the whisperX venv
```bash
cd ~/AIzunda/ttllm && ./install.sh
```

### 5. Pull & start VOICEVOX Docker
```bash
docker pull voicevox/voicevox_engine:cpu-ubuntu20.04-latest
docker run -d --name voicevox_engine --restart unless-stopped \
  -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu20.04-latest
```

### 6. Place the VRM model
`~/AIzunda/zundavrm/VRM/Zundamon_2025_VRM10A.vrm`

(Adjust `VRM_DIR` / filename in `three-vrm/server.py` as needed.)

---

## Launch sequence (every run)

Start 4 processes in order. Use systemd or tmux if you want them persistent.

### 1. VOICEVOX (Docker)
```bash
docker start voicevox_engine
# verify
curl -s http://localhost:50021/version
```

### 2. llama-server (LLM)
```bash
cd ~/AIzunda/llama.cpp/build/bin
./llama-server \
    -m ~/AIzunda/qwen3.6/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf \
    --host 127.0.0.1 --port 8080 \
    -ngl 99 -c 8192
```

### 3. ttllm bridge (WhisperX + LLM)
```bash
cd ~/AIzunda/ttllm && ./run.sh
# Swagger UI at http://localhost:8001/docs
curl -X POST http://localhost:8001/warmup  # recommended: preload WhisperX
```

### 4. three-vrm (VRM viewer + VOICEVOX relay)
```bash
cd ~/AIzunda/three-vrm && python3 server.py
```

Open `http://localhost:8000/zundamon.html`, **click the page once** (unlocks
AudioContext and mic permission), and press 🎤 to speak.

---

## Ports / endpoints

| Service      | Port  | Key endpoints |
|--------------|-------|---------------|
| VOICEVOX     | 50021 | `/audio_query`, `/synthesis` |
| llama-server | 8080  | `/v1/chat/completions` (OpenAI-compatible) |
| ttllm        | 8001  | `/voice_chat`, `/chat`, `/transcribe`, `/warmup`, `/health` |
| three-vrm    | 8000  | `/zundamon.html`, `/voice_chat_speak`, `/speak`, `/ws`, `/vrm/*` |

### `/voice_chat_speak` (one-shot API)

multipart/form-data:

| Field         | Type            | Default       | Description |
|---------------|-----------------|---------------|-------------|
| `audio`       | file            | —             | webm / wav / mp3 / m4a etc. |
| `speaker_id`  | int             | `3`           | VOICEVOX speaker ID (3 = Normal Zundamon) |
| `system`      | str             | ttllm default | Override LLM system prompt |
| `history`     | str (JSON list) | `[]`          | Conversation history |
| `temperature` | float           | `0.7`         | LLM |
| `max_tokens`  | int             | `512`         | LLM |

Response:
```json
{"ok": true, "transcript": "...", "reply": "...", "visemes": 42, "clients": 1}
```

Synthesized audio + viseme data is broadcast over WebSocket to every connected
client (not included in the HTTP response body).

---

## Browser UI

Built into `zundamon.html`:

- **🎤 button (bottom-right)**
  - **Long press (≥ 250 ms)**: records while held, releases to send (PTT)
  - **Short click**: start recording → click again to send (toggle)
- **Subtitles**
  - Pale blue: user speech transcription
  - White: Zundamon's reply
- **Lip-sync**
  - VRM 1.0 standard expressions `aa / ih / ou / ee / oh / nn` scheduled
    against `audioCtx.currentTime`

On first visit, **click the page once** to unlock AudioContext and mic permission.

---

## Zundamon speaker IDs

| Style     | ID |
|-----------|----|
| Normal    | 3  |
| Amaama    | 1  |
| Tsuntsun  | 7  |
| Sexy      | 5  |
| Whisper   | 22 |
| Hisohiso  | 38 |
| Heroero   | 75 |
| Namidame  | 76 |

Edit the `SPEAKER_ID` constant at the top of `zundamon.html` to change the
default.

---

## Smoke tests

```bash
# Service reachability
curl -s http://localhost:50021/version
curl -s http://localhost:8080/health
curl -s http://localhost:8001/health

# Text → VOICEVOX → VRM lip-sync (via three-vrm)
curl -X POST http://localhost:8000/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"こんにちはなのだ","speaker_id":3}'

# Text-only chat via ttllm (no VRM)
curl -X POST http://localhost:8001/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"自己紹介してなのだ"}'

# Audio file → STT + LLM + TTS + browser lip-sync
curl -X POST http://localhost:8000/voice_chat_speak \
  -F "audio=@sample.wav" -F "speaker_id=3"
```

---

## Known issues & caveats

### 1. WhisperX memory fault above 60 seconds
Happens with ROCm 7.x + PyTorch nightly.
```
Memory access fault by GPU node-1... Reason: Page not present or supervisor privilege.
```
Workaround: chunk to under 60 s on the client, or use
`clip_timestamps=[0, 60]` via faster-whisper directly. On the browser, just
stop the `MediaRecorder` before that limit.

### 2. three.js r170+ two-file layout
You need both `three.module.js` and `three.core.js` — without the latter,
Chrome throws the misleading `Failed to fetch dynamically imported module`
(actually a dependency-resolution failure). Both files must be present in
`libs/three/`.

### 3. Stateless
`/chat`, `/voice_chat`, and `/voice_chat_speak` all forget history. For
multi-turn conversations, pass prior turns as a JSON list via `history` on
every call.

### 4. AudioContext / mic permissions
Browser user-gesture policy requires an initial click. `zundamon.html` shows a
"click to enable audio" overlay on load; the click both unlocks AudioContext
and enables the 🎤 button.

### 5. VOICEVOX on CPU
Deliberate: avoids VRAM contention with ROCm. If long-reply latency matters,
use `speed_scale` or pre-split the text.

---

## Possible extensions

- **Persistent conversation history**: maintain a session store on three-vrm
  and coordinate with the browser
- **Streaming responses**: use llama.cpp SSE to synthesize per phrase, reducing
  time-to-first-audio
- **VAD auto-stop**: run webrtcvad / silero-wasm in the browser so 🎤 long-press
  isn't needed
- **Multiple characters**: map `speaker_id` to VRM files, add a character picker
- **Emotions**: have the LLM emit `<emotion>...</emotion>` and map to VRM 1.0
  `happy / sad / angry` expressions

---

## References & licenses

- WhisperX: https://github.com/m-bain/whisperX (BSD-4-Clause)
- CTranslate2: https://github.com/OpenNMT/CTranslate2 (MIT)
- llama.cpp: https://github.com/ggerganov/llama.cpp (MIT)
- VOICEVOX: https://voicevox.hiroshiba.jp/ (check terms of use and per-character licensing)
- Zundamon VRM: see `~/AIzunda/zundavrm/Zundamon_vn3license_*.pdf`
- three-vrm: https://github.com/pixiv/three-vrm (MIT)

Component-level READMEs:
- `~/AIzunda/whisperX-rocm/README.md`
- `~/AIzunda/ttllm/README.md`
- `~/AIzunda/voicevox/README.md`
- `~/AIzunda/three-vrm/README.md`
- `~/CLAUDE.md` (ROCm environment notes)
