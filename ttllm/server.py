import json
import os
import tempfile
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "ja")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_BATCH_SIZE = int(os.getenv("WHISPER_BATCH_SIZE", "8"))
WHISPER_VAD_METHOD = os.getenv("WHISPER_VAD_METHOD", "silero")

LLAMA_SERVER_URL = os.getenv("LLAMA_SERVER_URL", "http://localhost:8080").rstrip("/")
LLAMA_TIMEOUT = float(os.getenv("LLAMA_TIMEOUT", "120"))

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "あなたはずんだもんです。一人称は「ボク」、語尾に「のだ」を付けて、親しみやすく簡潔に話してください。",
)

_model = None
_whisperx = None


def _load_whisperx():
    global _whisperx
    if _whisperx is None:
        try:
            import whisperx
        except ImportError as e:
            raise HTTPException(
                503,
                "whisperx is not installed in the active venv. "
                "Run `cd ~/AIzunda/whisperX-rocm && uv pip install -e .` first.",
            ) from e
        _whisperx = whisperx
    return _whisperx


def get_model():
    global _model
    if _model is None:
        wx = _load_whisperx()
        _model = wx.load_model(
            WHISPER_MODEL,
            WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
            language=WHISPER_LANGUAGE,
            vad_method=WHISPER_VAD_METHOD,
        )
    return _model


app = FastAPI(title="ttllm bridge", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    text: str
    history: List[Message] = []
    system: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 512


class ChatResponse(BaseModel):
    reply: str


class TranscribeResponse(BaseModel):
    transcript: str


class VoiceChatResponse(BaseModel):
    transcript: str
    reply: str


def _transcribe_path(path: str) -> str:
    model = get_model()
    wx = _load_whisperx()
    audio = wx.load_audio(path)
    try:
        result = model.transcribe(audio, batch_size=WHISPER_BATCH_SIZE)
    except IndexError:
        # Silero VAD が発話なしと判定すると WhisperX 内部で inputs[0] が
        # IndexError を投げる。無音扱いで空文字を返す。
        return ""
    segments = result.get("segments", []) if isinstance(result, dict) else []
    return "".join(seg.get("text", "") for seg in segments).strip()


async def _save_upload(upload: UploadFile) -> str:
    suffix = Path(upload.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(await upload.read())
        return f.name


def _build_messages(
    user_text: str,
    system: Optional[str],
    history: List[dict],
) -> List[dict]:
    messages: List[dict] = []
    sys_msg = system if system is not None else SYSTEM_PROMPT
    if sys_msg:
        messages.append({"role": "system", "content": sys_msg})
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    return messages


async def _call_llama(messages: List[dict], temperature: float, max_tokens: int) -> str:
    payload = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        # Qwen3 系は既定で thinking を吐くので、chat template 側で切る。
        # これを渡さないと reasoning_content に数百トークン食われて
        # content が空のまま max_tokens に到達する。
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        async with httpx.AsyncClient(timeout=LLAMA_TIMEOUT) as client:
            r = await client.post(
                f"{LLAMA_SERVER_URL}/v1/chat/completions", json=payload
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"llama-server error: {e}") from e

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise HTTPException(502, f"unexpected llama-server response: {data}") from e


@app.get("/health")
async def health():
    llama_reachable = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{LLAMA_SERVER_URL}/health")
            llama_reachable = r.status_code < 500
    except httpx.HTTPError:
        pass
    return {
        "ok": True,
        "whisper": {
            "model": WHISPER_MODEL,
            "device": WHISPER_DEVICE,
            "loaded": _model is not None,
        },
        "llama": {"url": LLAMA_SERVER_URL, "reachable": llama_reachable},
    }


@app.post("/warmup")
async def warmup():
    get_model()
    return {"loaded": True}


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(audio: UploadFile = File(...)):
    path = await _save_upload(audio)
    try:
        text = _transcribe_path(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return TranscribeResponse(transcript=text)


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    messages = _build_messages(
        req.text, req.system, [m.model_dump() for m in req.history]
    )
    reply = await _call_llama(messages, req.temperature, req.max_tokens)
    return ChatResponse(reply=reply)


@app.post("/voice_chat", response_model=VoiceChatResponse)
async def voice_chat(
    audio: UploadFile = File(...),
    system: Optional[str] = Form(None),
    history: Optional[str] = Form(None),
    temperature: float = Form(0.7),
    max_tokens: int = Form(512),
):
    path = await _save_upload(audio)
    try:
        transcript = _transcribe_path(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if not transcript:
        return VoiceChatResponse(transcript="", reply="")

    parsed_history: List[dict] = []
    if history:
        try:
            raw = json.loads(history)
            parsed_history = [
                {"role": m["role"], "content": m["content"]} for m in raw
            ]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise HTTPException(400, f"invalid history JSON: {e}")

    messages = _build_messages(transcript, system, parsed_history)
    reply = await _call_llama(messages, temperature, max_tokens)
    return VoiceChatResponse(transcript=transcript, reply=reply)


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.post("/voice_chat_stream")
async def voice_chat_stream(
    audio: UploadFile = File(...),
    system: Optional[str] = Form(None),
    history: Optional[str] = Form(None),
    temperature: float = Form(0.7),
    max_tokens: int = Form(512),
):
    """STT → LLM をストリームし、SSE で {transcript, token..., done} を返す。"""
    path = await _save_upload(audio)
    try:
        transcript = _transcribe_path(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    parsed_history: List[dict] = []
    if history:
        try:
            raw = json.loads(history)
            parsed_history = [
                {"role": m["role"], "content": m["content"]} for m in raw
            ]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise HTTPException(400, f"invalid history JSON: {e}")

    async def event_stream():
        yield _sse({"type": "transcript", "text": transcript})

        if not transcript:
            yield _sse({"type": "done", "reply": ""})
            return

        messages = _build_messages(transcript, system, parsed_history)
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "chat_template_kwargs": {"enable_thinking": False},
        }

        reply_parts: List[str] = []
        try:
            async with httpx.AsyncClient(timeout=LLAMA_TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    f"{LLAMA_SERVER_URL}/v1/chat/completions",
                    json=payload,
                ) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        try:
                            delta = chunk["choices"][0].get("delta") or {}
                        except (KeyError, IndexError):
                            continue
                        text = delta.get("content") or ""
                        if text:
                            reply_parts.append(text)
                            yield _sse({"type": "token", "text": text})
        except httpx.HTTPError as e:
            yield _sse({"type": "error", "error": f"llama-server: {e}"})

        yield _sse({"type": "done", "reply": "".join(reply_parts)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
