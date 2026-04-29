"""
voiceflow/audio.py

Microphone capture using sounddevice (wraps PipeWire via ALSA compat).
Streams audio chunks into an asyncio Queue for the STT consumer.

Standalone test:
    python -m voiceflow.audio        # records 3s, saves /tmp/voiceflow_test.wav
    python -m voiceflow.audio --list # lists available devices
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import wave
from pathlib import Path
from typing import AsyncIterator

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"


class AudioCapture:
    """
    Push-to-talk mic capture.

    Call start_recording() when hotkey is pressed.
    Call stop_recording() when hotkey is released.
    Iterate audio_chunks() to get numpy arrays of PCM audio.
    amplitude property gives 0.0–1.0 level for overlay visualisation.
    """

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        chunk_ms: int = 100,
        device: str | None = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = int(sample_rate * chunk_ms / 1000)
        self.device = device

        self._chunk_queue: asyncio.Queue[np.ndarray | None] = asyncio.Queue()
        self._amplitude: float = 0.0
        self._recording = False
        self._stream = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def amplitude(self) -> float:
        """Current mic amplitude 0.0–1.0, updated in real time."""
        return self._amplitude

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start_recording(self, loop: asyncio.AbstractEventLoop) -> None:
        """Open the mic stream. Call from any thread."""
        if self._recording:
            return
        self._loop = loop
        self._recording = True
        self._chunk_queue = asyncio.Queue()

        try:
            import sounddevice as sd
        except ImportError:
            log.error("sounddevice not installed. Run: pip install sounddevice")
            return

        def callback(indata: np.ndarray, frames: int, time, status):
            if status:
                log.debug("sounddevice status: %s", status)
            if not self._recording:
                return
            chunk = indata.copy().flatten().astype(np.int16)
            # Update amplitude for overlay (RMS normalised to 0-1)
            rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
            self._amplitude = min(rms / 8000.0, 1.0)  # 8000 ≈ comfortable speech level
            # Thread-safe push to asyncio queue
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._chunk_queue.put_nowait, chunk)

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=DTYPE,
            blocksize=self.chunk_size,
            device=self.device,
            callback=callback,
        )
        self._stream.start()
        log.info("Mic stream opened (%.0f Hz, %dch)", self.sample_rate, self.channels)

    def stop_recording(self) -> None:
        """Close the mic stream. Call from any thread."""
        if not self._recording:
            return
        self._recording = False
        self._amplitude = 0.0
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                log.debug("Stream close error: %s", e)
            self._stream = None
        # Signal end-of-stream to consumers
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._chunk_queue.put_nowait, None)
        log.info("Mic stream closed")

    async def audio_chunks(self) -> AsyncIterator[np.ndarray]:
        """
        Async iterator — yields PCM chunks as numpy int16 arrays.
        Stops when stop_recording() is called (sentinel None in queue).
        """
        while True:
            chunk = await self._chunk_queue.get()
            if chunk is None:
                break
            yield chunk

    def collect_full_audio(self) -> bytes:
        """
        Drain the queue synchronously and return all audio as raw bytes.
        Call only after stop_recording().
        """
        chunks = []
        try:
            while True:
                chunk = self._chunk_queue.get_nowait()
                if chunk is None:
                    break
                chunks.append(chunk)
        except asyncio.QueueEmpty:
            pass
        if not chunks:
            return b""
        return np.concatenate(chunks).tobytes()


def list_devices() -> None:
    """Print all available audio input devices."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        print("\nAvailable audio devices:")
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                marker = " ← default" if i == sd.default.device[0] else ""
                print(f"  [{i:2d}] {d['name']}{marker}")
    except ImportError:
        print("sounddevice not installed")


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import wave

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")

    if "--list" in sys.argv:
        list_devices()
        sys.exit(0)

    RECORD_SECS = 3
    OUT_FILE = "/tmp/voiceflow_test.wav"

    print(f"Recording {RECORD_SECS}s of audio → {OUT_FILE}")
    print("Speak now...")

    async def _test():
        loop = asyncio.get_event_loop()
        cap = AudioCapture()
        cap.start_recording(loop)

        chunks = []
        start = asyncio.get_event_loop().time()

        async for chunk in cap.audio_chunks():
            chunks.append(chunk)
            elapsed = asyncio.get_event_loop().time() - start
            bars = int(cap.amplitude * 20)
            print(f"\r  Level: {'█' * bars:<20} {elapsed:.1f}s", end="", flush=True)
            if elapsed >= RECORD_SECS:
                cap.stop_recording()

        print()

        if chunks:
            audio = np.concatenate(chunks)
            with wave.open(OUT_FILE, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)  # int16 = 2 bytes
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio.tobytes())
            print(f"Saved {len(audio) / SAMPLE_RATE:.2f}s of audio to {OUT_FILE}")
        else:
            print("No audio captured")

    asyncio.run(_test())
