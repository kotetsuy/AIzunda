# AIzunda — Voice-chat AI pipeline with Zundamon

A fully local stack that runs **voice → STT → LLM → TTS → VRM lip-sync** end-to-end
on Ubuntu + AMD Ryzen AI Max+ 395 (ROCm). Click the 🎤 button in the browser and
Zundamon answers you by voice.

```
Browser (three-vrm)
  └─ Mic capture (MediaRecorder webm/opus)
         ↓ POST /voice_chat_speak_stream
    three-vrm server (port 8000)
         ↓ POST /voice_chat_stream
       ttllm bridge (port 8001)
         ├─ WhisperX-ROCm (STT, large-v3)
         └─ llama-server (Qwen3.6-35B-A3B, port 8080)
         ↓ SSE token stream
    three-vrm: split at sentence boundaries → VOICEVOX (port 50021) → WS broadcast
         ↓ WS (audio + visemes)
 Browser: AudioContext continuous playback + VRM lip-sync + background + idle motion
```

## Components

| Path | Role | Port |
|---|---|---|
| `voicevox/` | VOICEVOX Engine (Docker, CPU inference) | 50021 |
| `~/AIzunda/llama.cpp/build/bin/llama-server` | Qwen3.6 inference | 8080 |
| `qwen3.6/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` | LLM weights | — |
| `ttllm/` | FastAPI bridge (WhisperX + llama.cpp) | 8001 |
| `three-vrm/` | aiohttp server + VRM viewer (HTML/three-vrm) | 8000 |
| `vtt/` | CLI PTT mic (optional) | — |
| `images/` | VRM viewer background (rotates every 5 min) | — |
| `zundavrm/VRM/Zundamon_2025_VRM10A.vrm` | Zundamon VRM 1.0 model | — |
| `whisperX-rocm/` | WhisperX ROCm fork (venv at `~/AIzunda/whisperX-rocm/.venv`) | — |

### Prerequisites

- **OS**: Ubuntu 24.04.4 LTS
- **GPU**: AMD Ryzen AI Max+ 395 / Radeon 8060S (gfx1151, 48GB VRAM)
- **ROCm**: 7.2.1 (`/opt/rocm`)
- **Python**: 3.12.3
- **Docker**: 29.x (for VOICEVOX)
- **Browser**: Google Chrome (Firefox also works since `AudioContext` is supported)
- **tmux / curl**: used by the launch script

See each subdirectory's `README.md` for detailed setup:
`ttllm/README.md` / `vtt/README.md` / `three-vrm/README.md` / `voicevox/README.md` /
`whisperX-rocm/README.md`.

## Start / stop everything

```bash
~/AIzunda/start_all.sh   # start everything + health checks + WhisperX warmup + open Chrome
~/AIzunda/stop_all.sh    # stop the tmux session + VOICEVOX
~/AIzunda/stop_all.sh --keep-voicevox   # leave the VOICEVOX container running
```

`start_all.sh` creates a tmux session named `aizunda` and runs each service in its own window.

| window | command |
|---|---|
| 0 voicevox | `docker logs -f voicevox_engine` |
| 1 llama | `llama-server -m Qwen3.6... --port 8080 -ngl 99 -c 8192` |
| 2 ttllm | `ttllm/run.sh` (uvicorn) |
| 3 three-vrm | `python3 three-vrm/server.py` |
| 4 vtt | `vtt/run.sh --device USB` (CLI PTT, optional) |

Watch logs: `tmux attach -t aizunda`
Shut everything down: `~/AIzunda/stop_all.sh`

Startup order is serialized by dependency, with an HTTP health-check wait between
stages (the llama-server model load has a 600-second timeout). Right after ttllm
comes up, the script posts to `/warmup` to preload the WhisperX model so the
first utterance isn't slow.

## Using it in the browser

1. `start_all.sh` automatically opens Chrome at `http://localhost:8000/zundamon.html`
2. Click the page once to unlock AudioContext (browser user-gesture requirement)
3. The **🎤 button** at bottom-right
   - **Long-press (≥ 250 ms)**: records only while held, sends on release
   - **Short click**: starts recording → click again to send
4. User speech appears as pale-blue subtitles; Zundamon's reply as white subtitles

## Latency optimizations

Short utterances (e.g. "hello") land at ~1 s of perceived latency; even long replies
target **first audio in ~1 second**.

### 1. Disable Qwen3 thinking mode

By default, Qwen3 emits several hundred tokens of `reasoning_content` (internal
monologue) before the reply, costing multiple seconds of felt latency. ttllm
passes `chat_template_kwargs: {"enable_thinking": false}` to llama-server to
disable it (`ttllm/server.py:_call_llama`). This one line alone shaves 4–8 s off
the LLM stage.

### 2. Pipeline LLM → VOICEVOX

- ttllm adds `/voice_chat_stream` (SSE) and calls llama-server with
  `stream: true`, returning `{transcript}` → `{token}×N` → `{done}`.
- three-vrm's `/voice_chat_speak_stream` consumes the SSE. It splits on
  `[。！？\n]`; as a long-line fallback, anything over 60 chars is also split on
  `[、]`. TTS is serialized via `asyncio.Queue` + consumer task (to preserve WS
  ordering) while LLM decoding continues in parallel.
- The client resets its playhead on `turn_start` and queues each `speak` chunk
  at `startAt = max(playheadTime, now)` so chunks play back-to-back. Visemes are
  scheduled at absolute timestamps, so they don't interfere across chunks.

Measured result (long 8-sentence reply):

| Metric | Before (non-streaming) | After (pipelined) |
|---|---|---|
| Time to first audio | **3.32 s** | **1.06 s** |
| Total completion | 3.32 s | 2.98 s |

### 3. Cut previous speech when a new turn starts

When the mic is pressed, the client calls `stop(0)` on every scheduled
`AudioBufferSourceNode` and clears the viseme queue (`stopAllPlayback`). It
doesn't wait for the server's `turn_start` to arrive, so the response feels
instant.

## VRM viewer presentation

### Random background rotation

- Images are auto-discovered from `~/AIzunda/images/*.{jpg,png,webp}`
- `GET /images_list` returns the file list; `GET /images/<name>` serves them
- One is picked at page load; **every 5 minutes** it switches to another random image (`zundamon.html`)
- No images are bundled. Drop more images into the directory to add them (no server restart required)

### Idle motion

To avoid a stiff T-pose, a small rotation is applied each frame
(`zundamon.html:applyIdlePose`).

| Part | Frequency | Amplitude |
|---|---|---|
| spine / chest (X, breathing) | 0.25 Hz | ±0.7° |
| spine / chest (Z, sway) | 0.13 Hz (phase-shifted) | ±1.1° |
| head (X) | 0.10 Hz | ±0.9° |
| head (Y) | 0.08 Hz | ±1.7° |

Since the pose is set before `vrm.update(delta)`, the VRM's spring bones
(hair, skirt, etc.) follow naturally as secondary motion.

### Lower both arms

The VRM default is T-pose, so right after load `applyRestPose()` drops both arms
to a natural standing position and bends the elbows about 14° (`zundamon.html`).

## Key endpoints

### ttllm (port 8001)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Self + llama-server reachability |
| POST | `/warmup` | Preload WhisperX model |
| POST | `/transcribe` | Audio → text |
| POST | `/chat` | Text → LLM reply (non-streaming) |
| POST | `/voice_chat` | Audio → reply (non-streaming) |
| POST | `/voice_chat_stream` | Audio → SSE (transcript + token + done) **new** |

### three-vrm (port 8000)

| Method | Path | Purpose |
|---|---|---|
| GET | `/zundamon.html` | Viewer |
| GET | `/ws` | WebSocket (turn_start / speak / turn_end / transcript / error) |
| POST | `/speak` | Speak a given text |
| POST | `/voice_chat_speak` | Audio → one-shot reply (non-streaming) |
| POST | `/voice_chat_speak_stream` | Audio → pipelined reply **new** |
| GET | `/images_list` | Background image list |
| GET | `/images/{name}` | Serve a background image |
| GET | `/vrm/{name}` | Serve the VRM file |
| GET | `/status` | Number of connected clients |

## Known limitations

- **WhisperX segfaults on GPU for audio longer than 60 s** (known issue with
  ROCm 7.x + PyTorch nightly). The vtt CLI hard-caps each utterance at 55 s via
  VAD to work around this; avoid long recordings from the browser too.
- **Empty-audio utterances previously 500'd**. When Silero VAD returned
  "No active speech", WhisperX raised IndexError. Now caught in
  `_transcribe_path` and mapped to an empty string (`ttllm/server.py`).
- **VOICEVOX runs on CPU** deliberately, to avoid VRAM contention with ROCm.
  Fine for short text in real-time; long replies may become synthesis-bound.
- **Chrome's AudioContext** requires an initial click (user-gesture requirement).
- **Qwen3 thinking** is always off when you go through ttllm, but if you hit
  llama-server directly you need to pass `chat_template_kwargs` yourself.

## A note on paths

All hardcoded paths in shell scripts and Python have been replaced with
`$USER` / `os.path.expanduser("~/...")`. No `/home/<someone>` remains. To run
as a different user, just keep the directory layout (`~/AIzunda/`,
`~/AIzunda/llama.cpp/`, `~/AIzunda/whisperX-rocm/.venv/`) and it works.

## Troubleshooting

| Symptom | Fix |
|---|---|
| 🎤 press produces no audio | Click the page to unlock AudioContext. Check mic permissions in the browser |
| Zundamon stays silent / 500 errors | `tmux attach -t aizunda` to read ttllm logs. `curl :8001/health` to confirm llama reachability |
| First utterance is slow | `curl -X POST :8001/warmup` to preload WhisperX |
| Arms point the wrong way (after swapping VRM) | Flip the sign of `rotation.z` in `zundamon.html:applyRestPose` |
| Background doesn't rotate | Check `/images_list` in DevTools. Reload the browser after dropping in new images |
| VRM fails to load | Verify `server.py`'s `VRM_DIR` matches your filesystem. The filename must match `VRM_URL` in `zundamon.html` |
| Stop everything | `~/AIzunda/stop_all.sh` |

## Summary

The goal: a cloud-free "Zundamon you can talk to", running fully locally on a
single AMD Ryzen AI Max+ 395 + ROCm machine. With Qwen3.6-35B-A3B thinking-mode
disabled and the LLM→TTS pipeline, first audio lands in about a second, and
minimal code gives a non-jarring idle motion and background presentation.

Room for extension:

- Conversation history (currently stateless per turn — just pass `history`)
- VRMA-format idle animation loading (currently procedural)
- Swap VOICEVOX for a GPU build (speeds up synthesis of long replies)
- Switch to a smaller STT model (medium can trim 200–300 ms)
- Gesture syncing during LLM streaming
