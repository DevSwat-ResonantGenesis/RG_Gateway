from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import wave

import httpx
from fastapi import WebSocket

from .config import settings

logger = logging.getLogger(__name__)


@dataclass
class VoiceSessionState:
    session_id: str
    user_id: str
    started: bool = False
    sample_rate_hz: int = 16000
    encoding: str = "pcm16"
    chat_id: Optional[str] = None
    chunk_count: int = 0
    transcript_buffer: list[str] = field(default_factory=list)
    audio_buffer: bytearray = field(default_factory=bytearray)
    turn_index: int = 0
    interrupted_tts_count: int = 0
    asr_latency_ms: list[float] = field(default_factory=list)
    assistant_latency_ms: list[float] = field(default_factory=list)
    tts_latency_ms: list[float] = field(default_factory=list)
    active_tts_task: Optional[asyncio.Task] = None


@dataclass
class VoiceHealthCounters:
    sessions_started: int = 0
    sessions_active: int = 0
    sessions_completed: int = 0
    asr_turns: int = 0
    assistant_turns: int = 0
    tts_turns: int = 0
    barges: int = 0
    errors: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sessions_started": self.sessions_started,
            "sessions_active": self.sessions_active,
            "sessions_completed": self.sessions_completed,
            "asr_turns": self.asr_turns,
            "assistant_turns": self.assistant_turns,
            "tts_turns": self.tts_turns,
            "barges": self.barges,
            "errors": self.errors,
        }


class VoiceSessionSkeleton:
    """Gateway-level WebSocket runtime for /api/v1/voice/session.

    Runtime features:
    - WebSocket auth handshake
    - session lifecycle events
    - real ASR adapter (OpenAI Whisper, when key exists)
    - final ASR -> Resonant chat SSE streaming -> assistant token deltas
    - real TTS adapter (OpenAI TTS, when key exists) streamed in chunks
    - barge-in (interrupt TTS when new speech arrives)
    - turn/session metrics persistence + health counters
    """

    ASR_TRIGGER_BYTES = 96_000  # 3 seconds at 16kHz PCM16 — enough for reliable language detection
    TTS_CHUNK_BYTES = 4096

    def __init__(self) -> None:
        self._sessions: Dict[int, VoiceSessionState] = {}
        self._health = VoiceHealthCounters()
        self._metrics_path = os.getenv("VOICE_METRICS_PATH", "/tmp/voice_metrics.jsonl")

        self._asr_provider = os.getenv("VOICE_ASR_PROVIDER", "openai").strip().lower()
        self._tts_provider = os.getenv("VOICE_TTS_PROVIDER", "openai").strip().lower()
        self._llm_provider = os.getenv("VOICE_LLM_PROVIDER", "groq").strip().lower()

        self._openai_api_key = os.getenv("OPENAI_API_KEY")
        self._deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
        self._elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
        self._elevenlabs_voice_id = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        await self._send(
            websocket,
            {
                "type": "voice.session.awaiting_auth",
                "protocol_version": "v1alpha",
                "timestamp": self._now(),
            },
        )

        user_id = await self._authenticate(websocket)
        if not user_id:
            return

        ws_id = id(websocket)
        state = VoiceSessionState(session_id=f"voice-{uuid.uuid4().hex}", user_id=user_id)
        self._sessions[ws_id] = state
        self._health.sessions_started += 1
        self._health.sessions_active += 1

        await self._send(
            websocket,
            {
                "type": "voice.session.ready",
                "session_id": state.session_id,
                "user_id": state.user_id,
                "protocol_version": "v1alpha",
                "capabilities": {
                    "audio_input": True,
                    "asr_stream": self._asr_available(),
                    "assistant_stream": True,
                    "tts_stream": self._tts_available(),
                    "barge_in": True,
                    "asr_provider": self._asr_provider,
                    "tts_provider": self._tts_provider,
                    "llm_provider": self._llm_provider,
                },
                "timestamp": self._now(),
            },
        )

        try:
            while True:
                incoming = await websocket.receive()

                if incoming.get("type") == "websocket.disconnect":
                    break

                if incoming.get("bytes") is not None:
                    await self._handle_audio_bytes(websocket, state, incoming["bytes"])
                    continue

                text_payload = incoming.get("text")
                if text_payload is None:
                    continue

                await self._handle_text_message(websocket, state, text_payload)

        except Exception as exc:
            self._health.errors += 1
            logger.warning("Voice session websocket loop ended with error: %s", exc)
        finally:
            if state.active_tts_task and not state.active_tts_task.done():
                state.active_tts_task.cancel()
            self._sessions.pop(ws_id, None)
            self._health.sessions_active = max(0, self._health.sessions_active - 1)
            self._health.sessions_completed += 1
            self._persist_session_metrics(state)
            try:
                await websocket.close()
            except Exception:
                pass

    async def _authenticate(self, websocket: WebSocket) -> Optional[str]:
        # Fast path: browser sends rg_access_token cookie automatically on WS upgrade
        cookie_token = websocket.cookies.get("rg_access_token", "")
        if cookie_token:
            user_id = await self._verify_token(cookie_token)
            if user_id:
                await self._send(
                    websocket,
                    {
                        "type": "auth.success",
                        "user_id": user_id,
                        "mode": "cookie",
                        "timestamp": self._now(),
                    },
                )
                return user_id

        try:
            payload = await asyncio.wait_for(websocket.receive_json(), timeout=20.0)
        except asyncio.TimeoutError:
            await self._send(websocket, {"type": "error", "error": "Authentication timeout"})
            await websocket.close(code=4001)
            return None
        except Exception:
            await self._send(websocket, {"type": "error", "error": "Authentication message required"})
            await websocket.close(code=4001)
            return None

        if payload.get("type") != "auth":
            await self._send(websocket, {"type": "error", "error": "First message must be auth"})
            await websocket.close(code=4001)
            return None

        token = payload.get("token")
        provided_user_id = payload.get("user_id")

        if token:
            user_id = await self._verify_token(token)
            if user_id:
                await self._send(
                    websocket,
                    {
                        "type": "auth.success",
                        "user_id": user_id,
                        "timestamp": self._now(),
                    },
                )
                return user_id

            await self._send(websocket, {"type": "error", "error": "Invalid auth token"})
            await websocket.close(code=4002)
            return None

        if settings.DEV_MODE and provided_user_id:
            await self._send(
                websocket,
                {
                    "type": "auth.success",
                    "user_id": provided_user_id,
                    "mode": "dev_bypass",
                    "timestamp": self._now(),
                },
            )
            return provided_user_id

        await self._send(websocket, {"type": "error", "error": "Authentication required"})
        await websocket.close(code=4002)
        return None

    async def _verify_token(self, token: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{settings.AUTH_URL}/auth/verify",
                    json={"token": token},
                    timeout=5.0,
                )
            if response.status_code != 200:
                return None
            body = response.json()
            if body.get("valid"):
                return body.get("user_id")
            return None
        except Exception as exc:
            logger.warning("Voice WS token verification failed: %s", exc)
            return None

    async def _handle_text_message(self, websocket: WebSocket, state: VoiceSessionState, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            if message == "ping":
                await self._send(websocket, {"type": "pong", "timestamp": self._now()})
                return
            await self._send(websocket, {"type": "error", "error": "Invalid JSON payload"})
            return

        event_type = payload.get("type")

        if event_type == "ping":
            await self._send(websocket, {"type": "pong", "timestamp": self._now()})
            return

        if event_type == "session.start":
            state.started = True
            state.sample_rate_hz = int(payload.get("sample_rate_hz", 16000) or 16000)
            state.encoding = str(payload.get("encoding", "pcm16"))
            await self._send(
                websocket,
                {
                    "type": "session.started",
                    "session_id": state.session_id,
                    "sample_rate_hz": state.sample_rate_hz,
                    "encoding": state.encoding,
                    "timestamp": self._now(),
                },
            )
            return

        if event_type == "audio.chunk":
            chunk = payload.get("audio")
            if isinstance(chunk, str):
                try:
                    audio_bytes = base64.b64decode(chunk)
                except Exception:
                    audio_bytes = chunk.encode("utf-8")
                await self._handle_audio_chunk(websocket, state, audio_bytes)
                return
            await self._send(websocket, {"type": "error", "error": "audio.chunk requires string field 'audio'"})
            return

        if event_type == "health.get":
            await self._send(
                websocket,
                {
                    "type": "health.snapshot",
                    "session_id": state.session_id,
                    "health": self._health.to_dict(),
                    "timestamp": self._now(),
                },
            )
            return

        if event_type == "session.stop":
            await self._send(
                websocket,
                {
                    "type": "session.stopped",
                    "session_id": state.session_id,
                    "chunks_processed": state.chunk_count,
                    "transcript_turns": len(state.transcript_buffer),
                    "timestamp": self._now(),
                },
            )
            state.started = False
            return

        await self._send(
            websocket,
            {
                "type": "warning",
                "warning": f"Unsupported event type: {event_type}",
                "timestamp": self._now(),
            },
        )

    async def _handle_audio_bytes(self, websocket: WebSocket, state: VoiceSessionState, data: bytes) -> None:
        await self._handle_audio_chunk(websocket, state, data)

    async def _handle_audio_chunk(self, websocket: WebSocket, state: VoiceSessionState, chunk_bytes: bytes) -> None:
        state.chunk_count += 1
        state.audio_buffer.extend(chunk_bytes)

        # Barge-in: new user speech interrupts current TTS playback.
        if state.active_tts_task and not state.active_tts_task.done():
            state.active_tts_task.cancel()
            state.interrupted_tts_count += 1
            self._health.barges += 1
            await self._send(
                websocket,
                {
                    "type": "tts.interrupted",
                    "session_id": state.session_id,
                    "reason": "barge_in",
                    "timestamp": self._now(),
                },
            )

        await self._send(
            websocket,
            {
                "type": "audio.chunk.ack",
                "session_id": state.session_id,
                "sequence": state.chunk_count,
                "bytes": len(chunk_bytes),
                "timestamp": self._now(),
            },
        )

        # Emit lightweight VAD-style partial activity notifications.
        if state.chunk_count % 2 == 0:
            await self._send(
                websocket,
                {
                    "type": "asr.partial",
                    "session_id": state.session_id,
                    "text": "listening...",
                    "timestamp": self._now(),
                },
            )

        if len(state.audio_buffer) < self.ASR_TRIGGER_BYTES:
            return

        audio_snapshot = bytes(state.audio_buffer)
        state.audio_buffer.clear()

        turn_start = time.perf_counter()
        transcript = await self._transcribe_audio(audio_snapshot, state.sample_rate_hz)
        state.asr_latency_ms.append((time.perf_counter() - turn_start) * 1000)

        if not transcript:
            return

        state.turn_index += 1
        state.transcript_buffer.append(transcript)
        self._health.asr_turns += 1

        await self._send(
            websocket,
            {
                "type": "asr.final",
                "session_id": state.session_id,
                "turn": state.turn_index,
                "text": transcript,
                "timestamp": self._now(),
            },
        )

        await self._run_assistant_turn(websocket, state, transcript)

    async def _run_assistant_turn(self, websocket: WebSocket, state: VoiceSessionState, transcript: str) -> None:
        assistant_started = time.perf_counter()
        assistant_text, chat_id = await self._stream_resonant_reply(websocket, state, transcript)
        state.assistant_latency_ms.append((time.perf_counter() - assistant_started) * 1000)
        if chat_id:
            state.chat_id = chat_id

        if not assistant_text:
            return

        self._health.assistant_turns += 1
        await self._send(
            websocket,
            {
                "type": "assistant.done",
                "session_id": state.session_id,
                "turn": state.turn_index,
                "text": assistant_text,
                "timestamp": self._now(),
            },
        )

        # Stream TTS back on same session.
        tts_task = asyncio.create_task(self._stream_tts(websocket, state, assistant_text))
        state.active_tts_task = tts_task
        try:
            await tts_task
        except asyncio.CancelledError:
            pass
        finally:
            if state.active_tts_task is tts_task:
                state.active_tts_task = None

    async def _stream_resonant_reply(self, websocket: WebSocket, state: VoiceSessionState, transcript: str) -> tuple[str, Optional[str]]:
        payload = {
            "message": transcript,
            "chat_id": state.chat_id,
            "preferred_provider": self._llm_provider,
        }
        headers = {"x-user-id": state.user_id}

        full_text = ""
        discovered_chat_id: Optional[str] = None

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                async with client.stream(
                    "POST",
                    f"{settings.CHAT_URL}/resonant-chat/message/stream",
                    headers=headers,
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        if not raw:
                            continue
                        try:
                            evt = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        event_type = evt.get("event")
                        if event_type == "start":
                            discovered_chat_id = evt.get("chat_id") or discovered_chat_id
                        elif event_type == "chunk":
                            token = evt.get("content", "")
                            if token:
                                full_text += token
                                await self._send(
                                    websocket,
                                    {
                                        "type": "assistant.delta",
                                        "session_id": state.session_id,
                                        "turn": state.turn_index,
                                        "text": token,
                                        "timestamp": self._now(),
                                    },
                                )
                        elif event_type == "done":
                            discovered_chat_id = evt.get("chat_id") or discovered_chat_id
                        elif event_type == "error":
                            await self._send(
                                websocket,
                                {
                                    "type": "error",
                                    "error": evt.get("error", "assistant stream error"),
                                    "timestamp": self._now(),
                                },
                            )
                            return full_text, discovered_chat_id
        except Exception as exc:
            self._health.errors += 1
            logger.warning("Voice assistant stream failed: %s", exc)
            await self._send(
                websocket,
                {
                    "type": "error",
                    "error": f"assistant stream failure: {exc}",
                    "timestamp": self._now(),
                },
            )

        return full_text, discovered_chat_id

    async def _transcribe_audio(self, pcm_data: bytes, sample_rate_hz: int) -> str:
        if self._asr_provider == "deepgram":
            text = await self._transcribe_with_deepgram(pcm_data, sample_rate_hz)
            if text:
                return text
            # Fallback to OpenAI if configured but Deepgram fails
            if self._openai_api_key:
                logger.warning("Deepgram ASR returned empty result, falling back to OpenAI Whisper")
                return await self._transcribe_with_openai(pcm_data, sample_rate_hz)
            return ""

        if self._asr_provider == "openai":
            return await self._transcribe_with_openai(pcm_data, sample_rate_hz)

        logger.warning("Unsupported VOICE_ASR_PROVIDER=%s", self._asr_provider)
        return ""

    async def _transcribe_with_openai(self, pcm_data: bytes, sample_rate_hz: int) -> str:
        if not self._openai_api_key:
            return ""
        wav_bytes = self._pcm_to_wav_bytes(pcm_data, sample_rate_hz)
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        # No 'language' param → Whisper auto-detects (supports Russian, Ukrainian, Arabic, etc.)
        data = {"model": "whisper-1", "response_format": "text", "temperature": "0"}
        headers = {"Authorization": f"Bearer {self._openai_api_key}"}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers=headers,
                    data=data,
                    files=files,
                )
            if response.status_code >= 400:
                self._health.errors += 1
                logger.warning("OpenAI ASR failed status=%s body=%s", response.status_code, response.text[:300])
                return ""
            return response.text.strip()
        except Exception as exc:
            self._health.errors += 1
            logger.warning("OpenAI ASR request error: %s", exc)
            return ""

    async def _transcribe_with_deepgram(self, pcm_data: bytes, sample_rate_hz: int) -> str:
        if not self._deepgram_api_key:
            return ""
        wav_bytes = self._pcm_to_wav_bytes(pcm_data, sample_rate_hz)
        headers = {
            "Authorization": f"Token {self._deepgram_api_key}",
            "Content-Type": "audio/wav",
        }
        url = "https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true&punctuate=true&detect_language=true"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers, content=wav_bytes)
            if response.status_code >= 400:
                self._health.errors += 1
                logger.warning("Deepgram ASR failed status=%s body=%s", response.status_code, response.text[:300])
                return ""
            body = response.json()
            return (
                body.get("results", {})
                .get("channels", [{}])[0]
                .get("alternatives", [{}])[0]
                .get("transcript", "")
                .strip()
            )
        except Exception as exc:
            self._health.errors += 1
            logger.warning("Deepgram ASR request error: %s", exc)
            return ""

    async def _stream_tts(self, websocket: WebSocket, state: VoiceSessionState, text: str) -> None:
        if self._tts_provider == "elevenlabs":
            ok = await self._stream_tts_with_elevenlabs(websocket, state, text)
            if ok:
                return
            if self._openai_api_key:
                logger.warning("ElevenLabs TTS failed; falling back to OpenAI TTS")
                await self._stream_tts_with_openai(websocket, state, text)
                return
            return

        if self._tts_provider == "openai":
            await self._stream_tts_with_openai(websocket, state, text)
            return

        await self._send(
            websocket,
            {
                "type": "tts.unavailable",
                "session_id": state.session_id,
                "reason": f"Unsupported VOICE_TTS_PROVIDER={self._tts_provider}",
                "timestamp": self._now(),
            },
        )

    async def _stream_tts_with_openai(self, websocket: WebSocket, state: VoiceSessionState, text: str) -> None:
        if not self._openai_api_key:
            await self._send(
                websocket,
                {
                    "type": "tts.unavailable",
                    "session_id": state.session_id,
                    "reason": "OPENAI_API_KEY missing",
                    "timestamp": self._now(),
                },
            )
            return

        started = time.perf_counter()
        headers = {
            "Authorization": f"Bearer {self._openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o-mini-tts",
            "voice": "alloy",
            "input": text,
            "format": "mp3",
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers=headers,
                    json=payload,
                )
            response.raise_for_status()

            audio = response.content
            for idx in range(0, len(audio), self.TTS_CHUNK_BYTES):
                chunk = audio[idx : idx + self.TTS_CHUNK_BYTES]
                await self._send(
                    websocket,
                    {
                        "type": "tts.chunk",
                        "session_id": state.session_id,
                        "turn": state.turn_index,
                        "audio_format": "mp3",
                        "sequence": (idx // self.TTS_CHUNK_BYTES) + 1,
                        "audio": base64.b64encode(chunk).decode("utf-8"),
                        "timestamp": self._now(),
                    },
                )

            await self._send(
                websocket,
                {
                    "type": "tts.done",
                    "session_id": state.session_id,
                    "turn": state.turn_index,
                    "timestamp": self._now(),
                },
            )
            state.tts_latency_ms.append((time.perf_counter() - started) * 1000)
            self._health.tts_turns += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._health.errors += 1
            logger.warning("OpenAI TTS request error: %s", exc)
            await self._send(
                websocket,
                {
                    "type": "error",
                    "error": f"openai tts failure: {exc}",
                    "timestamp": self._now(),
                },
            )

    async def _stream_tts_with_elevenlabs(self, websocket: WebSocket, state: VoiceSessionState, text: str) -> bool:
        if not self._elevenlabs_api_key:
            await self._send(
                websocket,
                {
                    "type": "tts.unavailable",
                    "session_id": state.session_id,
                    "reason": "ELEVENLABS_API_KEY missing",
                    "timestamp": self._now(),
                },
            )
            return False

        started = time.perf_counter()
        headers = {
            "xi-api-key": self._elevenlabs_api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
        }
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self._elevenlabs_voice_id}?output_format=mp3_44100_128"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            audio = response.content

            for idx in range(0, len(audio), self.TTS_CHUNK_BYTES):
                chunk = audio[idx : idx + self.TTS_CHUNK_BYTES]
                await self._send(
                    websocket,
                    {
                        "type": "tts.chunk",
                        "session_id": state.session_id,
                        "turn": state.turn_index,
                        "audio_format": "mp3",
                        "sequence": (idx // self.TTS_CHUNK_BYTES) + 1,
                        "audio": base64.b64encode(chunk).decode("utf-8"),
                        "timestamp": self._now(),
                    },
                )

            await self._send(
                websocket,
                {
                    "type": "tts.done",
                    "session_id": state.session_id,
                    "turn": state.turn_index,
                    "timestamp": self._now(),
                },
            )
            state.tts_latency_ms.append((time.perf_counter() - started) * 1000)
            self._health.tts_turns += 1
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._health.errors += 1
            logger.warning("ElevenLabs TTS request error: %s", exc)
            await self._send(
                websocket,
                {
                    "type": "error",
                    "error": f"elevenlabs tts failure: {exc}",
                    "timestamp": self._now(),
                },
            )
            return False

    def _asr_available(self) -> bool:
        if self._asr_provider == "deepgram":
            return bool(self._deepgram_api_key) or bool(self._openai_api_key)
        if self._asr_provider == "openai":
            return bool(self._openai_api_key)
        return False

    def _tts_available(self) -> bool:
        if self._tts_provider == "elevenlabs":
            return bool(self._elevenlabs_api_key) or bool(self._openai_api_key)
        if self._tts_provider == "openai":
            return bool(self._openai_api_key)
        return False

    def _persist_session_metrics(self, state: VoiceSessionState) -> None:
        record = {
            "session_id": state.session_id,
            "user_id": state.user_id,
            "turns": state.turn_index,
            "chunks": state.chunk_count,
            "barges": state.interrupted_tts_count,
            "asr_latency_ms": state.asr_latency_ms,
            "assistant_latency_ms": state.assistant_latency_ms,
            "tts_latency_ms": state.tts_latency_ms,
            "finished_at": self._now(),
        }
        try:
            os.makedirs(os.path.dirname(self._metrics_path), exist_ok=True)
            with open(self._metrics_path, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(record) + "\n")
        except Exception as exc:
            logger.warning("Failed to persist voice metrics: %s", exc)

    @staticmethod
    def _pcm_to_wav_bytes(pcm_data: bytes, sample_rate_hz: int) -> bytes:
        out = io.BytesIO()
        with wave.open(out, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate_hz)
            wav_file.writeframes(pcm_data)
        return out.getvalue()

    async def _send(self, websocket: WebSocket, payload: Dict[str, Any]) -> None:
        await websocket.send_json(payload)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


voice_session_skeleton = VoiceSessionSkeleton()
