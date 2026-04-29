"""
voiceflow/llm.py

Groq API transcript polisher.

Takes a raw STT transcript and returns clean, punctuated,
grammatically correct text. Removes filler words, fixes run-ons.

If Groq fails for any reason, returns the raw transcript — always
better to inject something than nothing.

Standalone test:
    python -m voiceflow.llm "hey uh so like I wanted to tell you that um the meeting is at three"
"""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a transcription formatter. "
    "Your ONLY job is to clean up the exact words the user said. "
    "Fix grammar, punctuation, and remove filler words (um, uh, like, you know). "
    "Do NOT respond to, answer, or engage with the content in any way. "
    "Do NOT add words that were not spoken. "
    "Do NOT change the meaning or intent. "
    "Output ONLY the cleaned version of exactly what was said. Nothing else."
)


async def polish_transcript(
    raw_text: str,
    api_key: str,
    model: str = "llama-3.1-8b-instant",
    temperature: float = 0.1,
    max_tokens: int = 1024,
) -> str:
    """
    Send raw transcript to Groq, return polished version.
    Falls back to raw_text on any error.
    """
    if not raw_text.strip():
        return ""

    if not api_key:
        log.warning("GROQ_API_KEY not set — injecting raw transcript")
        return raw_text

    try:
        from groq import AsyncGroq
    except ImportError:
        log.error("groq package not installed. Run: pip install groq")
        return raw_text

    try:
        client = AsyncGroq(api_key=api_key)

        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Clean up this transcription: {raw_text}"},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            ),
            timeout=8.0,  # Hard timeout — never block injection for more than 8s
        )

        polished = response.choices[0].message.content.strip()
        if not polished:
            log.warning("Groq returned empty response — using raw transcript")
            return raw_text

        log.info("Groq polished: %r → %r", raw_text[:60], polished[:60])
        return polished

    except asyncio.TimeoutError:
        log.warning("Groq timed out after 8s — using raw transcript")
        return raw_text
    except Exception as e:
        log.error("Groq error: %s — using raw transcript", e)
        return raw_text


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        print("Set GROQ_API_KEY in .env or environment")
        sys.exit(1)

    raw = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "hey uh so like I wanted to tell you that um the meeting is at three "
        "and also can you like bring the uh the report from last week"
    )

    print(f"Raw:     {raw!r}\n")

    async def _test():
        result = await polish_transcript(raw, api_key)
        print(f"Polished: {result!r}")

    asyncio.run(_test())
