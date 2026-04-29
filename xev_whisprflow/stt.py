"""
voiceflow/stt.py

Deepgram pre-recorded (REST) speech-to-text client.
Sends collected audio as a single HTTP request — simpler and more reliable
than WebSocket streaming for push-to-talk use cases.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

log = logging.getLogger(__name__)


class DeepgramSTT:
    def __init__(
        self,
        api_key: str,
        model: str = "nova-3",
        language: str = "en",
        sample_rate: int = 16000,
        channels: int = 1,
        endpointing_ms: int = 300,
        on_interim: Callable[[str], None] | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.language = language
        self.sample_rate = sample_rate
        self.channels = channels
        self.on_interim = on_interim
        self._audio_chunks: list[bytes] = []

    async def __aenter__(self):
        self._audio_chunks = []
        return self

    async def __aexit__(self, *args):
        pass

    async def send_audio(self, chunk: bytes) -> None:
        if isinstance(chunk, bytes):
            self._audio_chunks.append(chunk)
        else:
            self._audio_chunks.append(chunk.tobytes())

    async def finalize(self) -> str:
        if not self._audio_chunks:
            return ""

        audio_bytes = b"".join(self._audio_chunks)

        try:
            import httpx
        except ImportError:
            raise RuntimeError("httpx not installed. Run: pip install httpx")

        if not self.api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is not set")

        url = (
            f"https://api.deepgram.com/v1/listen"
            f"?model={self.model}"
            f"&language={self.language}"
            f"&encoding=linear16"
            f"&sample_rate={self.sample_rate}"
            f"&channels={self.channels}"
            f"&smart_format=true"
            f"&punctuate=true"
        )

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "audio/raw",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, content=audio_bytes, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            results = data.get("results", {})
            channels = results.get("channels", [])
            if not channels:
                return ""
            alternatives = channels[0].get("alternatives", [])
            if not alternatives:
                return ""
            transcript = alternatives[0].get("transcript", "").strip()
            log.info("Deepgram transcript: %r", transcript)
            return transcript

        except httpx.HTTPStatusError as e:
            log.error("Deepgram HTTP error %d: %s", e.response.status_code, e.response.text)
            return ""
        except Exception as e:
            log.error("Deepgram request failed: %s", e)
            return ""


async def transcribe_audio_bytes(
    audio_bytes: bytes,
    api_key: str,
    model: str = "nova-3",
    language: str = "en",
    sample_rate: int = 16000,
) -> str:
    stt = DeepgramSTT(api_key=api_key, model=model, language=language, sample_rate=sample_rate)
    async with stt:
        await stt.send_audio(audio_bytes)
        return await stt.finalize()


if __name__ == "__main__":
    import sys
    import os
    from pathlib import Path

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    def load_dotenv_if_available():
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

    load_dotenv_if_available()

    api_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not api_key:
        print("Set DEEPGRAM_API_KEY in .env or environment")
        sys.exit(1)

    wav_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/voiceflow_test.wav"
    if not Path(wav_path).exists():
        print(f"File not found: {wav_path}")
        sys.exit(1)

    import wave
    with wave.open(wav_path, "rb") as wf:
        audio_bytes = wf.readframes(wf.getnframes())
        sr = wf.getframerate()

    print(f"Transcribing {wav_path}...")

    async def _test():
        result = await transcribe_audio_bytes(audio_bytes, api_key, sample_rate=sr)
        print(f"Transcript: {result!r}")

    asyncio.run(_test())
