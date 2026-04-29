# vtt — Mic → WhisperX transcription

A minimal CLI that captures audio from a USB mic (or any input device) on this
machine and sends it through the `ttllm` bridge to WhisperX for transcription.
It sits at the head of the AIzunda pipeline
(`vtt → ttllm → llama-server → voicevox → talkinghead/zundavrm`)
as the voice-input stage.

As of 2026-04-20, the USB mic input → WhisperX-ROCm transcription path is
verified to work end-to-end.

## Layout

```
vtt/
├── vtt.py       # CLI entry point
├── install.sh   # creates a local .venv and installs deps
├── run.sh       # execution wrapper
└── README.md    # this file
```

It is a thin client that POSTs WAV to `ttllm`'s `/transcribe`. WhisperX /
torch-ROCm / ctranslate2-rocm live on the ttllm side
(`~/AIzunda/whisperx-rocm`), so vtt itself only installs
`numpy` / `sounddevice` / `soundfile` / `httpx`.

## Verified configuration

| Item | Value |
| ---- | --- |
| OS | Ubuntu 24.04.4 LTS (PipeWire) |
| Input device | USB Composite Device (YunChen, card 1, 48 kHz mono) |
| ttllm endpoint | `http://localhost:8001` (`~/AIzunda/ttllm/run.sh`) |
| WhisperX venv | `~/whisperx-rocm/.venv` (torch 2.9.1+rocm7.2.0 / ctranslate2 4.6.2 / faster-whisper 1.2.1) |
| Model | `large-v3` (set via env on the ttllm side) |

Gotcha encountered:

- When PortAudio opens a USB ALSA device directly, 16 kHz is often refused.
  vtt automatically falls back to 48 kHz and lets WhisperX (ffmpeg) handle
  resampling.

## Prerequisites

- `~/AIzunda/ttllm` must be running and reachable at `http://localhost:8001`
  ```bash
  cd ~/AIzunda/ttllm && ./run.sh
  ```
- `~/AIzunda/whisperx-rocm` must contain whisperx, and ttllm's `WHISPERX_VENV`
  must point at `~/AIzunda/whisperx-rocm/.venv` (see ttllm's `README.md`).
- `libportaudio2` must be installed
  ```bash
  sudo apt-get install -y libportaudio2
  ```

## Setup

```bash
cd ~/AIzunda/vtt
./install.sh
```

## Usage

### Check devices

First make sure the mic is visible.

```bash
./run.sh --list-devices
```

On this setup it shows the following. Use `--device USB` to match by substring
(numeric IDs work too).

```
[4] USB Composite Device: Audio (hw:1,0)  in=1 sr=48000
[5] HD-Audio Generic: SN6186 Analog (hw:2,0)  in=2 sr=48000
[7] pipewire  in=64 sr=44100
[8] pulse  in=32 sr=44100
[9] default  in=64 sr=44100
```

### Push-to-talk (default)

Enter starts recording, Enter again stops and transcribes.

```bash
./run.sh --device USB
```

Sample run:

```
warming up WhisperX via ttllm...
Press Enter to START recording...
Recording. Press Enter to STOP.

テストテスト。聞こえますか?テストテスト。
```

### Fixed-duration recording

```bash
./run.sh --device USB --duration 5
```

### Continuous transcription via VAD

A mode that keeps transcribing as you speak, splitting on silences. Stop with
Ctrl+C. To dodge the ROCm "memory fault past 60 s" issue, each utterance is
hard-cut at 55 s.

```bash
./run.sh --device USB --vad
```

Sample run:

```
VAD listening (threshold=0.012, silence=0.8s). Ctrl+C to stop.
テストテスト聞こえますか?
これはコンティニューテストです
聞こえますか
^C
```

If a noisy environment causes false positives, raise `--vad-threshold`
(default 0.012, recommended range 0.02–0.05).

### Output options

| Option | Description |
| ------ | ----------- |
| `--output FILE` | Append transcripts to FILE |
| `--json`        | Emit one JSON line per utterance: `{"ts": ..., "transcript": ...}` |
| `--keep DIR`    | Keep recorded WAVs under DIR (for debugging) |
| `--no-warmup`   | Skip the `/warmup` POST |

Example: VAD continuous transcription with JSON logging.

```bash
./run.sh --device USB --vad --json --output ./transcripts.jsonl --keep ./captures
```

## Environment variables

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `VTT_SERVER`              | `http://localhost:8001` | URL of the ttllm bridge |
| `VTT_SAMPLE_RATE`         | `16000` | Capture sample rate |
| `VTT_CHANNELS`            | `1`     | Input channel count |
| `VTT_DEVICE`              | (none)  | Device index or substring match |
| `VTT_VAD_THRESHOLD`       | `0.012` | RMS threshold for VAD |
| `VTT_VAD_SILENCE_SEC`     | `0.8`   | Silence length that ends an utterance |
| `VTT_VAD_MIN_SPEECH_SEC`  | `0.3`   | Drop anything shorter than this |
| `VTT_VAD_MAX_SEC`         | `55`    | Maximum utterance length (< 60 s to dodge ROCm) |

## How it works

1. `sounddevice` captures `float32` / mono / 16 kHz from PortAudio (PipeWire
   backend). If the device refuses 16 kHz, vtt falls back to its native rate
   (48 kHz for USB mics) and lets WhisperX (via ffmpeg) handle the resample.
2. The buffer is written as PCM16 WAV and sent as multipart to
   `POST {VTT_SERVER}/transcribe`.
3. `{"transcript": "..."}` from ttllm is printed to stdout.

## Hooking up the next stage

Hit ttllm's `/voice_chat` instead of `/transcribe` to get transcription plus
the llama.cpp reply in one shot. To call directly from a browser
(`talkinghead` / `zundavrm`), see the JavaScript example in ttllm's
`README.md`. To go all the way to LLM from vtt, swap the call inside
`post_transcribe` to `/voice_chat` and emit the `reply` field too.

## Caveats

- `--vad` will treat loud HVAC or fans as continuous speech. Tune
  `--vad-threshold` up to around 0.02–0.05.
- Each utterance is cut at 55 s, so long readings get split automatically.
  Re-join on the caller side if you need.
- If ttllm is not running, `/transcribe` will SystemExit. Start it first
  in another terminal: `cd ~/AIzunda/ttllm && ./run.sh`.
- The first `/transcribe` takes tens of seconds while WhisperX loads. After
  that it stays warm as long as the ttllm process is alive — no reload needed.
- If you are RDP-ing into this box from a MacBook and want to use the Mac's
  mic, pass `--device` pointing at the RDP virtual input. `./run.sh
  --list-devices` shows the names.
