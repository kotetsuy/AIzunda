# vtt — Mic → WhisperX transcription

A minimal CLI that captures audio from a USB mic (or any input device) on this
machine, forwards it to the `ttllm` bridge, and gets it transcribed by WhisperX.
It covers the voice-input stage at the head of the AIzunda pipeline
(`vtt → ttllm → llama-server → voicevox → talkinghead/zundavrm`).

As of 2026-04-20, USB-mic input → WhisperX-ROCm transcription is confirmed
working end-to-end.

## Layout

```
vtt/
├── vtt.py       # CLI
├── install.sh   # creates a local .venv and installs deps
├── run.sh       # launch wrapper
└── README.md    # this file
```

It's a thin client that POSTs WAV to ttllm's `/transcribe`. WhisperX /
torch-ROCm / ctranslate2-rocm all live on the ttllm side
(`~/venv/whisperx-rocm`); vtt itself only installs `numpy` / `sounddevice` /
`soundfile` / `httpx`.

## Confirmed working configuration

| Item | Value |
|------|-------|
| OS | Ubuntu 24.04.4 LTS (PipeWire) |
| Input device | USB Composite Device (YunChen, card 1, 48 kHz mono) |
| ttllm target | `http://localhost:8001` (`~/AIzunda/ttllm/run.sh`) |
| WhisperX venv | `~/venv/whisperx-rocm` (torch 2.9.1+rocm7.2.0 / ctranslate2 4.6.2 / faster-whisper 1.2.1) |
| Model | `large-v3` (set via ttllm env vars) |

Pitfalls we hit:

- ttllm's default `WHISPERX_VENV` used to point to `~/AIzunda/whisperX-rocm/.venv`,
  but whisperX / torch-ROCm actually live in `~/venv/whisperx-rocm`. After
  pointing ttllm's `install.sh` / `run.sh` at `~/venv/whisperx-rocm` and
  re-running `./install.sh` to add the fastapi stack, `/transcribe` starts
  returning 200.
- Grabbing the USB ALSA device directly through PortAudio gets 16 kHz refused,
  so vtt automatically falls back to 48 kHz and leaves resampling to WhisperX
  (via ffmpeg).

## Prerequisites

- `~/AIzunda/ttllm` running and responding at `http://localhost:8001`
  ```bash
  cd ~/AIzunda/ttllm && ./run.sh
  ```
- whisperx installed in `~/venv/whisperx-rocm`, with ttllm's `WHISPERX_VENV`
  pointing there (see ttllm's `README.md`)
- `libportaudio2` installed
  ```bash
  sudo apt-get install -y libportaudio2
  ```

## Setup

```bash
cd ~/AIzunda/vtt
./install.sh
```

## Usage

### List devices

First confirm the mic is visible.

```bash
./run.sh --list-devices
```

On this machine it looks like:

```
[4] USB Composite Device: Audio (hw:1,0)  in=1 sr=48000
[5] HD-Audio Generic: SN6186 Analog (hw:2,0)  in=2 sr=48000
[7] pipewire  in=64 sr=44100
[8] pulse  in=32 sr=44100
[9] default  in=64 sr=44100
```

`--device USB` matches partially by name (numeric indices also work).

### Push-to-talk (default)

Enter to start, Enter again to stop and transcribe.

```bash
./run.sh --device USB
```

Example:

```
warming up WhisperX via ttllm...
Press Enter to START recording...
Recording. Press Enter to STOP.

テストテスト。聞こえますか?テストテスト。
```

### Fixed duration

```bash
./run.sh --device USB --duration 5
```

### VAD-driven continuous transcription

Keeps listening, splitting at silence. Ctrl+C to exit. To avoid the ROCm 60-s
memory fault, a single utterance is force-cut at 55 s.

```bash
./run.sh --device USB --vad
```

Example:

```
VAD listening (threshold=0.012, silence=0.8s). Ctrl+C to stop.
テストテスト聞こえますか?
これはコンティニューテストです
聞こえますか
^C
```

In noisy rooms raise `--vad-threshold`
(default 0.012, try 0.02–0.05).

### Output options

| Option             | Description |
|--------------------|-------------|
| `--output FILE`    | Append transcriptions to FILE |
| `--json`           | Emit `{"ts": ..., "transcript": ...}` one per line |
| `--keep DIR`       | Save captured WAVs to DIR (for debugging) |
| `--no-warmup`      | Skip the `/warmup` POST |

Example: VAD-driven continuous transcription with JSON log.

```bash
./run.sh --device USB --vad --json --output ./transcripts.jsonl --keep ./captures
```

## Environment variables

| Variable                  | Default                   | Description |
|---------------------------|---------------------------|-------------|
| `VTT_SERVER`              | `http://localhost:8001`   | ttllm bridge URL |
| `VTT_SAMPLE_RATE`         | `16000`                   | Capture sample rate |
| `VTT_CHANNELS`            | `1`                       | Input channels |
| `VTT_DEVICE`              | (unset)                   | Device number or partial-match name |
| `VTT_VAD_THRESHOLD`       | `0.012`                   | VAD RMS threshold |
| `VTT_VAD_SILENCE_SEC`     | `0.8`                     | Silence that marks end-of-utterance |
| `VTT_VAD_MIN_SPEECH_SEC`  | `0.3`                     | Discard anything shorter than this |
| `VTT_VAD_MAX_SEC`         | `55`                      | Max utterance length (< 60 s to dodge ROCm bug) |

## How it works

1. `sounddevice` grabs PortAudio (PipeWire backend) at `float32` / mono /
   16 kHz. If the device refuses 16 kHz, it falls back to the device's default
   rate (48 kHz for USB mics) and lets WhisperX (via ffmpeg) handle resampling.
2. Convert to PCM16 WAV and `POST` it to `{VTT_SERVER}/transcribe` as
   multipart.
3. Print the `{"transcript": "..."}` from ttllm to stdout.

## Wiring to the next stage

Swap `/transcribe` for ttllm's `/voice_chat` to get transcription + llama.cpp
reply in one shot. For calling from the browser (`talkinghead` / `zundavrm`),
see the JavaScript sample in `ttllm/README.md`. To go all the way from vtt
through the LLM, swap `post_transcribe` to call `/voice_chat` and also print
the `reply` field.

## Known caveats

- With `--vad`, loud fans / AC can register as constant speech. Bump
  `--vad-threshold` up to 0.02–0.05.
- Utterances are cut at 55 s, so long reads are split across multiple
  transcriptions. Concatenate on the caller if you need a single string.
- Running with ttllm down will `SystemExit` on `/transcribe`. Start it first
  with `cd ~/AIzunda/ttllm && ./run.sh` in another terminal.
- The very first `/transcribe` takes tens of seconds while WhisperX loads. As
  long as ttllm stays up, subsequent calls stay warm.
- If you're RDP'd in from a MacBook and want to use the Mac's mic, select the
  RDP virtual input with `--device` (visible in `./run.sh --list-devices`).
