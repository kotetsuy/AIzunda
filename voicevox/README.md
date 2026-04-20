# AIzunda — VOICEVOX setup

## Overview

The TTS (speech synthesis) component of the AIzunda pipeline. Runs VOICEVOX in
Docker and exposes an HTTP API that other components call for synthesis.

## Environment

- Docker: 29.4.0
- Image: `voicevox/voicevox_engine:cpu-ubuntu20.04-latest`
- API port: `50021`
- Inference: CPU (to avoid contention with ROCm)

## Launch

### First-time setup

```bash
docker pull voicevox/voicevox_engine:cpu-ubuntu20.04-latest

docker run -d \
  --name voicevox_engine \
  --restart unless-stopped \
  -p 50021:50021 \
  voicevox/voicevox_engine:cpu-ubuntu20.04-latest
```

### Verify

```bash
curl http://localhost:50021/version
```

### Stop / restart

```bash
docker stop voicevox_engine
docker start voicevox_engine
```

## Zundamon speaker IDs

| Style   | ID |
|---------|----|
| Normal  | 3  |
| Amaama  | 1  |
| Tsuntsun| 7  |
| Sexy    | 5  |
| Whisper | 22 |
| Hisohiso| 38 |
| Heroero | 75 |
| Namidame| 76 |

## Using the API

Synthesis is a two-step call.

### 1. audio_query (build the synthesis query)

```bash
curl -X POST "http://localhost:50021/audio_query" \
  --get \
  --data-urlencode "text=こんにちは、ずんだもんなのだ！" \
  --data-urlencode "speaker=3" \
  -o query.json
```

### 2. synthesis (produce the WAV)

```bash
curl -X POST "http://localhost:50021/synthesis?speaker=3" \
  -H "Content-Type: application/json" \
  -d @query.json \
  -o output.wav
```

### Python example

```python
import requests

def synthesize(text: str, speaker: int = 3, output_path: str = "output.wav"):
    base_url = "http://localhost:50021"

    query = requests.post(
        f"{base_url}/audio_query",
        params={"text": text, "speaker": speaker}
    ).json()

    wav = requests.post(
        f"{base_url}/synthesis",
        params={"speaker": speaker},
        json=query
    )

    with open(output_path, "wb") as f:
        f.write(wav.content)

synthesize("こんにちは、ずんだもんなのだ！")
```

## Test script

```bash
# Default (normal style, Japanese test sentence)
./test_voicevox.sh

# Specify text, speaker, and output path
./test_voicevox.sh "よろしくなのだ" 3 /tmp/test.wav
```

## Where this fits in the pipeline

```
Mic input
   ↓
WhisperX (STT) — ~/AIzunda/whisperX-rocm
   ↓ text
llama-server (LLM) — Qwen3.5-35B, localhost:8080
   ↓ reply text
VOICEVOX Engine (TTS) ← here — localhost:50021
   ↓ WAV
TalkingHead (VRM) — browser, lip-sync
```

## Output format

- Format: RIFF WAV
- Sample rate: 24,000 Hz
- Bit depth: 16 bit
- Channels: mono
