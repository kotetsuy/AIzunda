# AIzunda — VOICEVOX setup

## Overview

The TTS (speech synthesis) component of the AIzunda pipeline.
VOICEVOX runs in Docker and other components call it via HTTP.

## Environment

- Docker: 29.4.0
- Image: `voicevox/voicevox_engine:cpu-ubuntu20.04-latest`
- API port: `50021`
- Inference: CPU (to avoid contention with the ROCm runtime)

## How to run

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

| Style    | ID |
|----------|----|
| Normal   | 3  |
| Sweet    | 1  |
| Tsundere | 7  |
| Sexy     | 5  |
| Whisper  | 22 |
| Murmur   | 38 |
| Weak     | 75 |
| Tearful  | 76 |

## API usage

Synthesis is a two-step call.

### 1. audio_query (build the synthesis query)

```bash
curl -X POST "http://localhost:50021/audio_query" \
  --get \
  --data-urlencode "text=こんにちは、ずんだもんなのだ！" \
  --data-urlencode "speaker=3" \
  -o query.json
```

### 2. synthesis (generate WAV)

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
# Defaults (normal style, Japanese test sentence)
./test_voicevox.sh

# Specify text and speaker
./test_voicevox.sh "よろしくなのだ" 3 /tmp/test.wav
```

## Position in the pipeline

```
Mic input
   ↓
WhisperX (STT) — ~/AIzunda/whisperX-rocm
   ↓ text
llama-server (LLM) — Qwen3.5-35B, localhost:8080
   ↓ reply text
VOICEVOX Engine (TTS) ← here — localhost:50021
   ↓ WAV audio
TalkingHead (VRM) — browser-side lip-sync
```

## Output spec

- Format: RIFF WAV
- Sample rate: 24,000 Hz
- Bit depth: 16-bit
- Channels: mono
