#!/usr/bin/env python3
"""vtt — マイク入力を WhisperX(ttllm 経由) で文字起こしする CLI。

モード:
    --duration N   N 秒録音して転写
    --ptt          Enter で開始 / Enter で停止 (既定)
    --vad          無音区切りで連続転写 (Ctrl+C で終了)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import queue
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import httpx
import numpy as np
import sounddevice as sd
import soundfile as sf


DEFAULT_SERVER = os.getenv("VTT_SERVER", "http://localhost:8001")
DEFAULT_SAMPLE_RATE = int(os.getenv("VTT_SAMPLE_RATE", "16000"))
DEFAULT_CHANNELS = int(os.getenv("VTT_CHANNELS", "1"))
DEFAULT_DEVICE = os.getenv("VTT_DEVICE")

DEFAULT_VAD_THRESHOLD = float(os.getenv("VTT_VAD_THRESHOLD", "0.012"))
DEFAULT_VAD_SILENCE_SEC = float(os.getenv("VTT_VAD_SILENCE_SEC", "0.8"))
DEFAULT_VAD_MIN_SPEECH_SEC = float(os.getenv("VTT_VAD_MIN_SPEECH_SEC", "0.3"))
# ROCm 側の既知問題で 60s 超はメモリフォールトするので余裕をもって 55s
DEFAULT_VAD_MAX_SEC = float(os.getenv("VTT_VAD_MAX_SEC", "55"))


def resolve_device(dev: Optional[str]) -> Optional[int]:
    if dev is None or dev == "":
        return None
    try:
        return int(dev)
    except ValueError:
        pass
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and dev.lower() in d["name"].lower():
            return i
    raise SystemExit(f"no input device matching '{dev}'")


def list_devices() -> None:
    default_in = sd.default.device[0] if sd.default.device else None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            mark = " *" if i == default_in else ""
            print(
                f"[{i}] {d['name']}  (in={d['max_input_channels']}, "
                f"sr={int(d['default_samplerate'])}){mark}"
            )


def pick_sample_rate(sr: int, ch: int, device: Optional[int]) -> int:
    """Return requested sr if the device accepts it, else the device default."""
    try:
        sd.check_input_settings(device=device, channels=ch, samplerate=sr,
                                dtype="float32")
        return sr
    except Exception:
        info = sd.query_devices(device, "input")
        fallback = int(info["default_samplerate"])
        print(f"[warn] device does not accept {sr} Hz; falling back to "
              f"{fallback} Hz (ttllm/WhisperX will resample).",
              file=sys.stderr)
        return fallback


def wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def post_transcribe(server: str, audio: np.ndarray, sample_rate: int,
                    timeout: float = 120.0) -> str:
    payload = wav_bytes(audio, sample_rate)
    files = {"audio": ("capture.wav", payload, "audio/wav")}
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(f"{server.rstrip('/')}/transcribe", files=files)
            r.raise_for_status()
            return r.json().get("transcript", "")
    except httpx.HTTPError as e:
        raise SystemExit(f"ttllm /transcribe error: {e}")


def warmup(server: str, timeout: float = 180.0) -> None:
    print("warming up WhisperX via ttllm...", file=sys.stderr, flush=True)
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(f"{server.rstrip('/')}/warmup")
            r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"(warmup failed: {e})", file=sys.stderr)


def record_duration(sr: int, ch: int, device: Optional[int],
                    seconds: float) -> np.ndarray:
    print(f"recording {seconds:.1f}s...", file=sys.stderr, flush=True)
    audio = sd.rec(int(seconds * sr), samplerate=sr, channels=ch,
                   dtype="float32", device=device)
    sd.wait()
    return audio.flatten() if ch == 1 else audio


def record_ptt(sr: int, ch: int, device: Optional[int]) -> np.ndarray:
    print("Press Enter to START recording...", file=sys.stderr, end="", flush=True)
    try:
        input()
    except EOFError:
        return np.zeros(0, dtype="float32")
    print("Recording. Press Enter to STOP.", file=sys.stderr, flush=True)

    chunks: list[np.ndarray] = []

    def cb(indata, frames, time_info, status):
        if status:
            print(f"[audio status] {status}", file=sys.stderr)
        chunks.append(indata.copy())

    with sd.InputStream(samplerate=sr, channels=ch, dtype="float32",
                        device=device, callback=cb):
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

    if not chunks:
        return np.zeros(0, dtype="float32")
    audio = np.concatenate(chunks, axis=0)
    return audio.flatten() if ch == 1 else audio


def vad_loop(sr: int, ch: int, device: Optional[int],
             threshold: float, silence_sec: float,
             min_speech_sec: float, max_sec: float,
             on_utterance: Callable[[np.ndarray], None]) -> None:
    block_sec = 0.03
    block_size = max(1, int(sr * block_sec))
    silence_blocks = max(1, int(silence_sec / block_sec))
    max_blocks = max(1, int(max_sec / block_sec))
    min_speech_blocks = max(1, int(min_speech_sec / block_sec))
    pre_blocks = max(1, int(0.3 / block_sec))

    q: "queue.Queue[np.ndarray]" = queue.Queue()

    def cb(indata, frames, time_info, status):
        if status:
            print(f"[audio status] {status}", file=sys.stderr)
        q.put(indata.copy())

    print(
        f"VAD listening (threshold={threshold:.3f}, silence={silence_sec:.1f}s). "
        "Ctrl+C to stop.",
        file=sys.stderr,
    )

    in_speech = False
    active: list[np.ndarray] = []
    silent = 0
    pre: list[np.ndarray] = []

    with sd.InputStream(samplerate=sr, channels=ch, dtype="float32",
                        device=device, blocksize=block_size, callback=cb):
        try:
            while True:
                block = q.get()
                mono = block.mean(axis=1) if block.ndim > 1 else block
                rms = float(np.sqrt(np.mean(mono * mono))) if mono.size else 0.0
                is_speech = rms >= threshold
                if not in_speech:
                    pre.append(block)
                    if len(pre) > pre_blocks:
                        pre.pop(0)
                    if is_speech:
                        in_speech = True
                        active = list(pre)
                        pre = []
                        silent = 0
                else:
                    active.append(block)
                    silent = 0 if is_speech else silent + 1
                    if silent >= silence_blocks or len(active) >= max_blocks:
                        speech_blocks = len(active) - silent
                        if speech_blocks >= min_speech_blocks:
                            audio = np.concatenate(active, axis=0)
                            audio = audio.flatten() if ch == 1 else audio
                            on_utterance(audio)
                        in_speech = False
                        active = []
                        silent = 0
        except KeyboardInterrupt:
            print("", file=sys.stderr)


def make_emitter(args, sample_rate: int) -> Callable[[str, Optional[np.ndarray]], None]:
    def emit(transcript: str, audio: Optional[np.ndarray] = None):
        if not transcript:
            return
        if args.json:
            line = json.dumps(
                {"ts": time.time(), "transcript": transcript},
                ensure_ascii=False,
            )
        else:
            line = transcript
        print(line, flush=True)
        if args.output:
            with open(args.output, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        if args.keep and audio is not None and audio.size:
            Path(args.keep).mkdir(parents=True, exist_ok=True)
            fn = Path(args.keep) / f"vtt-{int(time.time() * 1000)}.wav"
            sf.write(fn, audio, sample_rate, subtype="PCM_16")

    return emit


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Mic -> text via ttllm/WhisperX bridge",
    )
    ap.add_argument("--list-devices", action="store_true",
                    help="list audio input devices and exit")
    ap.add_argument("--device", default=DEFAULT_DEVICE,
                    help="input device index or name substring "
                    "(env VTT_DEVICE). Example: --device USB")
    ap.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE,
                    help="capture rate; falls back to device default if "
                    "unsupported")
    ap.add_argument("--channels", type=int, default=DEFAULT_CHANNELS)
    ap.add_argument("--server", default=DEFAULT_SERVER,
                    help=f"ttllm bridge URL (default {DEFAULT_SERVER})")
    ap.add_argument("--no-warmup", action="store_true",
                    help="skip /warmup in vad/ptt modes")

    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--duration", type=float,
                      help="record N seconds then transcribe (one-shot)")
    mode.add_argument("--ptt", action="store_true",
                      help="push-to-talk: Enter starts, Enter stops (default)")
    mode.add_argument("--vad", action="store_true",
                      help="VAD-based continuous transcription")

    ap.add_argument("--vad-threshold", type=float, default=DEFAULT_VAD_THRESHOLD,
                    help=f"RMS gate for speech (default {DEFAULT_VAD_THRESHOLD})")
    ap.add_argument("--vad-silence", type=float, default=DEFAULT_VAD_SILENCE_SEC,
                    help="seconds of silence that end an utterance")
    ap.add_argument("--vad-min-speech", type=float,
                    default=DEFAULT_VAD_MIN_SPEECH_SEC,
                    help="drop utterances shorter than this (seconds)")
    ap.add_argument("--vad-max", type=float, default=DEFAULT_VAD_MAX_SEC,
                    help="force cut utterances longer than this (seconds)")

    ap.add_argument("--output", help="append transcripts to this file")
    ap.add_argument("--json", action="store_true",
                    help="emit {ts, transcript} JSON lines")
    ap.add_argument("--keep", metavar="DIR",
                    help="save captured WAVs to DIR for inspection")

    args = ap.parse_args()

    if args.list_devices:
        list_devices()
        return

    device = resolve_device(args.device)
    sr = pick_sample_rate(args.sample_rate, args.channels, device)
    emit = make_emitter(args, sr)

    def transcribe(audio: np.ndarray) -> None:
        if audio.size == 0:
            return
        text = post_transcribe(args.server, audio, sr)
        emit(text, audio)

    if args.vad:
        if not args.no_warmup:
            warmup(args.server)
        vad_loop(
            sr, args.channels, device,
            args.vad_threshold, args.vad_silence,
            args.vad_min_speech, args.vad_max,
            transcribe,
        )
        return

    if args.duration is not None:
        audio = record_duration(sr, args.channels, device, args.duration)
        transcribe(audio)
        return

    if not args.no_warmup:
        warmup(args.server)
    audio = record_ptt(sr, args.channels, device)
    if audio.size == 0:
        print("(no audio captured)", file=sys.stderr)
        return
    transcribe(audio)


if __name__ == "__main__":
    main()
