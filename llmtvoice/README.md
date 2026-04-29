# AIzunda — end-to-end voice-chat pipeline reference

A voice-chat system that runs entirely on an ROCm-capable AMD GPU: mic input in,
a Zundamon VRM model lip-syncing the reply in the browser out. Components are
loosely coupled HTTP services, so you can swap any of them out.

This document is the pipeline-level consolidation of each component's
individual README (`whisperX-rocm` / `ttllm` / `voicevox` / `three-vrm`).

---

## Big picture

```
┌──────────────────────┐
│ Browser (mic input)        │  http://localhost:8000/zundamon.html
│   MediaRecorder → webm    │
└──────────┬────────────┘
           │ multipart POST /voice_chat_speak
           ▼
┌──────────────────────┐
│ three-vrm server (port 8000) │  aiohttp
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
│ → WS broadcast            │
└──────────┬────────────┘
           ▼
┌──────────────────────┐
│ Browser: audio playback + lip-sync │
│  @pixiv/three-vrm 1.0 expressions │
│  (aa / ih / ou / ee / oh / nn)│
└──────────────────────┘
```

---

## Directory layout

```
~/AIzunda/
├── whisperX-rocm/       # STT (WhisperX + CTranslate2-ROCm)
├── ctranslate2-rocm/    # CTranslate2 with ROCm/HIP (built from source)
├── llama.cpp/           # LLM inference engine (llama-server)
├── qwen3.6/             # GGUF model files
├── ttllm/               # WhisperX ↔ llama.cpp bridge (FastAPI)
├── voicevox/            # VOICEVOX Docker launch + test scripts
├── three-vrm/           # VRM viewer + VOICEVOX relay (aiohttp)
│   └── TalkingHead/     # Browser front-end (zundamon.html)
├── zundavrm/            # Zundamon VRM model files
└── llmtvoice/           # This README (pipeline-level reference)
```

---

## Required environment

| Item    | Requirement |
| ------- | ----------- |
| OS      | Ubuntu 24.04 LTS |
| GPU     | AMD Ryzen AI Max+ 395 / Radeon 8060S (gfx1151, 48 GB VRAM) |
| ROCm    | 7.2.0 (`/opt/rocm`) |
| Python  | 3.12.3 |
| Docker  | 29.x (for VOICEVOX) |
| Node    | not needed (browser uses locally-served three.js, not a CDN) |

ROCm env vars are set inside each `run.sh`. For manual launches:
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

# Reinstall the ROCm-flavored ctranslate2 Python bindings
rm -rf .venv/lib/python3.12/site-packages/ctranslate2*
export CTRANSLATE2_ROOT=/usr/local
uv pip install --reinstall pybind11 ~/AIzunda/ctranslate2-rocm/python
```

### 3. Build llama.cpp
Follow llama.cpp's own `CLAUDE.md` / `AGENTS.md`. Build `llama-server` with
ROCm (HIP) support.

### 4. Add the ttllm bridge's deps to the WhisperX venv
```bash
cd ~/AIzunda/ttllm && ./install.sh
```

### 5. Pull and start VOICEVOX in Docker
```bash
docker pull voicevox/voicevox_engine:cpu-ubuntu20.04-latest
docker run -d --name voicevox_engine --restart unless-stopped \
  -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu20.04-latest
```

### 6. Drop in the VRM model
`~/AIzunda/zundavrm/VRM/Zundamon_2025_VRM10A.vrm`

(If you change the path or filename, update `VRM_DIR` in
`three-vrm/server.py`.)

---

## Daily startup

Bring the four processes up in order. For long-running setups, systemd or
tmux is recommended.

### ① VOICEVOX (Docker)
```bash
docker start voicevox_engine
# verify
curl -s http://localhost:50021/version
```

### ② llama-server (LLM)
```bash
cd ~/AIzunda/llama.cpp/build/bin
./llama-server \
    -m ~/AIzunda/qwen3.6/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf \
    --host 127.0.0.1 --port 8080 \
    -ngl 99 -c 8192
```

### ③ ttllm bridge (WhisperX + LLM)
```bash
cd ~/AIzunda/ttllm && ./run.sh
# Swagger UI at http://localhost:8001/docs
curl -X POST http://localhost:8001/warmup  # preload WhisperX (recommended)
```

### ④ three-vrm (VRM viewer + VOICEVOX relay)
```bash
cd ~/AIzunda/three-vrm && python3 server.py
```

Open `http://localhost:8000/zundamon.html` in a browser, **click once** to
unlock AudioContext and mic permission, then talk via the 🎤 button at
bottom-right.

---

## Ports / endpoints summary

| Service       | Port  | Main endpoints |
| ------------- | ----- | -------------- |
| VOICEVOX      | 50021 | `/audio_query`, `/synthesis` |
| llama-server  | 8080  | `/v1/chat/completions` (OpenAI-compatible) |
| ttllm         | 8001  | `/voice_chat`, `/chat`, `/transcribe`, `/warmup`, `/health` |
| three-vrm     | 8000  | `/zundamon.html`, `/voice_chat_speak`, `/speak`, `/ws`, `/vrm/*` |

### `/voice_chat_speak` (one-shot API)

multipart/form-data:

| Field          | Type            | Default | Description |
| -------------- | --------------- | ------- | ----------- |
| `audio`        | file            | —       | webm / wav / mp3 / m4a etc. |
| `speaker_id`   | int             | `3`     | VOICEVOX speaker ID (3 = normal Zundamon) |
| `system`       | str             | ttllm default | Override LLM system prompt |
| `history`      | str (JSON list) | `[]`    | Conversation history |
| `temperature`  | float           | `0.7`   | LLM |
| `max_tokens`   | int             | `512`   | LLM |

Response:
```json
{"ok": true, "transcript": "...", "reply": "...", "visemes": 42, "clients": 1}
```

The synthesized audio + lip-sync data is broadcast over WebSocket to all
connected clients (it is **not** in the response body).

---

## Browser UI

Already wired into `zundamon.html`:

- **🎤 button at bottom-right**
  - **Long-press (≥ 250 ms)**: records only while held, sends on release (PTT)
  - **Short click**: starts recording → click again to send (toggle)
- **Subtitles**
  - Pale blue: user transcript
  - White:    Zundamon's reply
- **Lip-sync**
  - VRM 1.0 standard expressions `aa / ih / ou / ee / oh / nn`,
    scheduled against `audioCtx.currentTime`

**Click the page once** on first load to unlock AudioContext and mic permission.

---

## Zundamon speaker IDs

| Style       | ID |
| ----------- | -- |
| Normal      | 3  |
| Sweet       | 1  |
| Tsundere    | 7  |
| Sexy        | 5  |
| Whisper     | 22 |
| Murmur      | 38 |
| Weak        | 75 |
| Tearful     | 76 |

You can change the default in `SPEAKER_ID` near the top of `zundamon.html`.

---

## Smoke tests

```bash
# Per-service reachability
curl -s http://localhost:50021/version
curl -s http://localhost:8080/health
curl -s http://localhost:8001/health

# Text → VOICEVOX → VRM lip-sync via three-vrm
curl -X POST http://localhost:8000/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"こんにちはなのだ","speaker_id":3}'

# Text-only chat through ttllm (no VRM)
curl -X POST http://localhost:8001/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"自己紹介してなのだ"}'

# Audio file → transcript + LLM reply + synthesis + browser lip-sync
curl -X POST http://localhost:8000/voice_chat_speak \
  -F "audio=@sample.wav" -F "speaker_id=3"
```

---

## Known issues & caveats

### 1. WhisperX memory fault past 60 s
Triggered by ROCm 7.x + PyTorch nightly:
```
Memory access fault by GPU node-1... Reason: Page not present or supervisor privilege.
```
Mitigation: chunk into < 60 s pieces on the client, or use
`clip_timestamps=[0, 60]` directly with faster-whisper. In the browser, stop
`MediaRecorder` early to avoid it.

### 2. three.js r170+ is two files
You must place **both** `three.module.js` and `three.core.js`. Without
`three.core.js`, Chrome throws the misleading error
`Failed to fetch dynamically imported module` (the real cause is unresolved
dependencies). Both must live in `libs/three/`.

### 3. Stateless
`/chat`, `/voice_chat`, and `/voice_chat_speak` do not retain conversation
history. For continuous conversation, the caller must send prior turns via
the `history` field as a JSON list.

### 4. AudioContext / mic permission
Browser user-gesture policy requires an initial click. `zundamon.html` shows
a "click to enable audio" overlay on load; the click unlocks AudioContext
and activates the 🎤 button at the same time.

### 5. VOICEVOX runs on CPU
The CPU container is intentional — it avoids interference with the ROCm
runtime. If long-text latency matters, tune `speed_scale` or pre-split the
text.

---

## Possible extensions

- **Persisted conversation history**: a session store on the three-vrm side,
  coordinated with the browser
- **Streaming responses**: leverage llama.cpp's SSE; synthesize per phrase in
  VOICEVOX to start speaking earlier (cuts time-to-first-audio)
- **Browser-side VAD**: webrtcvad / silero-wasm to remove the long-press
  requirement
- **Multi-character**: bind `speaker_id` to a VRM file and add a character
  switcher
- **Emotional expressions**: have the LLM emit `<emotion>...</emotion>` and
  map it to VRM 1.0's `happy / sad / angry`

---

## References / licenses

- WhisperX: https://github.com/m-bain/whisperX (BSD-4-Clause)
- CTranslate2: https://github.com/OpenNMT/CTranslate2 (MIT)
- llama.cpp: https://github.com/ggerganov/llama.cpp (MIT)
- VOICEVOX: https://voicevox.hiroshiba.jp/ (check terms of use and per-character licenses)
- Zundamon VRM: see `~/AIzunda/zundavrm/Zundamon_vn3license_*.pdf`
- three-vrm: https://github.com/pixiv/three-vrm (MIT)

For per-component details:
- `~/AIzunda/whisperX-rocm/README.md`
- `~/AIzunda/ttllm/README.md`
- `~/AIzunda/voicevox/README.md`
- `~/AIzunda/three-vrm/README.md`
- `~/CLAUDE.md` (ROCm environment notes)
