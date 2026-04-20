# Zundamon three-vrm server

A standalone server that sends VOICEVOX synthesis results to the browser over
WebSocket and renders a VRM 1.0 Zundamon model with lip-sync via
`@pixiv/three-vrm`.

## Directory layout

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
- **ttllm bridge** (WhisperX + llama.cpp) on `localhost:8001`
  (only required for the mic-input flow — see `~/AIzunda/ttllm/run.sh`)
- **llama-server** on `localhost:8080` (ttllm depends on it)
- **Zundamon VRM** placed at `~/AIzunda/zundavrm/VRM/Zundamon_2025_VRM10A.vrm`
  (edit `VRM_DIR` in `server.py` if you want a different location)

## Full pipeline

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

## Launch

```bash
cd ~/AIzunda/three-vrm
python3 server.py
```

Open `http://localhost:8000/zundamon.html` in a browser.
On the first visit you need to **click the page once** to unlock AudioContext
(browser user-gesture requirement).

## Trigger audio + lip-sync

```bash
curl -X POST http://localhost:8000/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"こんにちはなのだ","speaker_id":3}'
```

- `text`: text to read
- `speaker_id`: Zundamon style
  - 3: Normal
  - 1: Amaama
  - 7: Tsuntsun
  - 22: Whisper

Response:
```json
{"ok": true, "visemes": 40, "clients": 1}
```

## How it works

1. `POST /speak` → VOICEVOX `audio_query` → `synthesis` produces WAV
2. Convert `accent_phrases` moras into `visemes / vtimes / vdurations`
   - Vowels: a→aa, i→I, u→U, e→E, o→O, N→nn
   - Consonants: p/b/m→PP, s/z→SS, t/d→DD, k/g→kk, …
   - Time unit: **milliseconds**
3. Broadcast a JSON message over WebSocket to every connected client
4. Browser: Base64 → WAV decode → play with `AudioContext`
5. Schedule `vrm.expressionManager.setValue(expr, 1.0)` against
   `audioCtx.currentTime`

Only VRM 1.0 standard expressions `aa / ih / ou / ee / oh / nn` move. Consonants
briefly close the mouth.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET  | `/zundamon.html` | Viewer (mic button included) |
| GET  | `/ws` | WebSocket connection |
| POST | `/speak` | Synthesize + broadcast lip-sync data |
| POST | `/voice_chat_speak` | Audio → ttllm → VOICEVOX → WS broadcast (one-shot) |
| GET  | `/vrm/{filename}` | Serve a VRM file |
| GET  | `/status` | Number of connected clients |

### `/voice_chat_speak` (multipart/form-data)

| Field          | Type            | Default     | Description |
|----------------|-----------------|-------------|-------------|
| `audio`        | file            | —           | webm / wav / mp3 / m4a etc. |
| `speaker_id`   | int             | `3`         | VOICEVOX speaker ID |
| `system`       | str             | ttllm default | Override LLM system prompt |
| `history`      | str (JSON list) | `[]`        | Conversation history |
| `temperature`  | float           | `0.7`       | LLM |
| `max_tokens`   | int             | `512`       | LLM |

Response:
```json
{"ok": true, "transcript": "...", "reply": "...", "visemes": 42, "clients": 1}
```

Browser example (already wired into the viewer):
```javascript
const fd = new FormData();
fd.append("audio", blob, "utterance.webm");
fd.append("speaker_id", "3");
await fetch("/voice_chat_speak", { method: "POST", body: fd });
// Response audio plays automatically with lip-sync via WS
```

### Built-in browser mic

The 🎤 button at bottom-right:
- **Long press (≥ 250 ms)**: records only while held (releases to send)
- **Short click**: start recording → click again to send
- User speech shows as pale-blue subtitles; Zundamon's reply in white

On first visit, click the page once to unlock AudioContext and mic permission.

## Gotchas when rebuilding

- **three.js r170+ is a two-file layout**: `three.module.js` + `three.core.js`.
  Both must be present. Missing `three.core.js` makes Chrome throw the
  misleading `Failed to fetch dynamically imported module` (actually a
  dependency-resolution failure).
  Source: `https://unpkg.com/three@0.180.0/build/three.core.js`
- `GLTFLoader.js` and `three-vrm.module.min.js` import the bare specifier
  `"three"`. This is resolved by the `<script type="importmap">` tag in
  `zundamon.html`.
- The server sends vtimes / vdurations in **milliseconds**. The browser
  compares against `audioCtx.currentTime` (seconds), so you must divide by 1000.

## Known warning (harmless)

```
VRMUtils.removeUnnecessaryJoints is deprecated. Use combineSkeletons instead.
```
Scheduled for removal in the next major version.

## Next step

Wire mic input → WhisperX (STT) → llama-server Qwen3-35B-A3B → this `/speak`
endpoint and you have a complete AI Zundamon.
