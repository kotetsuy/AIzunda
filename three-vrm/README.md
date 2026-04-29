# Zundamon three-vrm server

A standalone server that sends VOICEVOX synthesis results to the browser over
WebSocket and lip-syncs a VRM 1.0 model (Zundamon) using `@pixiv/three-vrm`.

## Layout

```
~/AIzunda/three-vrm/
├── server.py                      # aiohttp server (port 8000)
├── README.md
└── TalkingHead/
    ├── zundamon.html              # the viewer
    └── libs/
        ├── three/
        │   ├── three.module.js    # r180 wrapper
        │   ├── three.core.js      # r180 implementation (required)
        │   └── addons/
        │       ├── loaders/GLTFLoader.js
        │       └── utils/BufferGeometryUtils.js
        └── three-vrm/
            └── three-vrm.module.min.js
```

## Prerequisites

- **VOICEVOX engine** running on `localhost:50021` (Docker recommended)
  ```
  docker start $(docker ps -aq --filter ancestor=voicevox/voicevox_engine:cpu-ubuntu20.04-latest)
  ```
- **ttllm bridge** (WhisperX + llama.cpp) running on `localhost:8001`
  (only required if you want mic input; `~/AIzunda/ttllm/run.sh`)
- **llama-server** running on `localhost:8080` (a dependency of ttllm)
- **Zundamon VRM** placed at
  `/home/araki/AIzunda/zundavrm/VRM/Zundamon_2025_VRM10A.vrm`
  (change `VRM_DIR` in `server.py` if your path differs)

## Pipeline overview

```
Mic (browser zundamon.html)
    ↓ MediaRecorder (webm/opus)
three-vrm /voice_chat_speak           (port 8000)
    ↓ multipart POST audio
ttllm /voice_chat                     (port 8001)
    ↓ WhisperX STT → llama.cpp LLM
ttllm returns {transcript, reply}
    ↓ three-vrm receives reply
VOICEVOX /audio_query + /synthesis    (port 50021)
    ↓ WAV + accent_phrases
three-vrm: moras → visemes
    ↓ WS broadcast
Browser: AudioContext playback + three-vrm lip-sync
```

## Run

```bash
cd ~/AIzunda/three-vrm
python3 server.py
```

Open `http://localhost:8000/zundamon.html` in a browser. On first load,
**click the page once** to unlock AudioContext (user-gesture requirement).

## Trigger speech + lip-sync

```bash
curl -X POST http://localhost:8000/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"こんにちはなのだ","speaker_id":3}'
```

- `text`: text to read
- `speaker_id`: Zundamon style
  - 3: Normal
  - 1: Sweet
  - 7: Tsundere
  - 22: Whisper

Response:
```json
{"ok": true, "visemes": 40, "clients": 1}
```

## Internals

1. `POST /speak` → VOICEVOX `audio_query` → `synthesis` produces WAV
2. From `accent_phrases.moras`, compute `visemes / vtimes / vdurations`
   - vowels: a→aa, i→I, u→U, e→E, o→O, N→nn
   - consonants: p/b/m→PP, s/z→SS, t/d→DD, k/g→kk, etc.
   - time unit: **milliseconds**
3. Broadcast to all WebSocket clients as JSON
4. The browser does Base64 → WAV decode → `AudioContext` playback
5. Schedules against `audioCtx.currentTime` and fires
   `vrm.expressionManager.setValue(expr, 1.0)` per viseme

Only the VRM 1.0 standard expressions `aa / ih / ou / ee / oh / nn` are
animated. Consonants temporarily close the mouth.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET  | `/zundamon.html`     | Viewer (mic button included) |
| GET  | `/ws`                | WebSocket connection |
| POST | `/speak`             | Synthesize + broadcast lip-sync |
| POST | `/voice_chat_speak`  | Audio → ttllm → VOICEVOX → WS broadcast (one-shot) |
| GET  | `/vrm/{filename}`    | Serve a VRM file |
| GET  | `/status`            | Number of connected clients |

### `/voice_chat_speak` (multipart/form-data)

| Field          | Type            | Default | Description |
| -------------- | --------------- | ------- | ----------- |
| `audio`        | file            | —       | webm / wav / mp3 / m4a etc. |
| `speaker_id`   | int             | `3`     | VOICEVOX speaker ID |
| `system`       | str             | ttllm default | Override LLM system prompt |
| `history`      | str (JSON list) | `[]`    | Conversation history |
| `temperature`  | float           | `0.7`   | LLM |
| `max_tokens`   | int             | `512`   | LLM |

Response:
```json
{"ok": true, "transcript": "...", "reply": "...", "visemes": 42, "clients": 1}
```

Browser-side example (already wired into the viewer):
```javascript
const fd = new FormData();
fd.append("audio", blob, "utterance.webm");
fd.append("speaker_id", "3");
await fetch("/voice_chat_speak", { method: "POST", body: fd });
// Response audio plays back automatically with lip-sync over WS
```

### Browser mic

The 🎤 button at bottom-right:
- **Long-press (≥ 250 ms)**: records only while held (sends on release)
- **Short click**: starts recording → click again to send
- User speech shows as pale-blue subtitles, Zundamon's reply as white subtitles

Click the page once on first load to unlock AudioContext and mic permission.

## Pitfalls when rebuilding

- **three.js r170+ ships as two files: `three.module.js` + `three.core.js`.**
  Both are required.
  Without `three.core.js`, Chrome throws the misleading error
  `Failed to fetch dynamically imported module` (the real cause is unresolved
  dependencies).
  Source: `https://unpkg.com/three@0.180.0/build/three.core.js`
- `GLTFLoader.js` and `three-vrm.module.min.js` import the bare specifier
  `"three"`. The `<script type="importmap">` block in `zundamon.html` resolves it.
- The server's vtimes / vdurations are in **milliseconds**. The browser
  compares against `audioCtx.currentTime` (seconds), so divide by 1000.

## Known warnings (no functional impact)

```
VRMUtils.removeUnnecessaryJoints is deprecated. Use combineSkeletons instead.
```
Will be removed in the next major release.

## Next step

Mic input → WhisperX (STT) → llama-server Qwen3-35B-A3B → call this `/speak`:
that pipeline glue script is what turns this into a fully working AI Zundamon.
