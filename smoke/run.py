"""Explicitly opt-in, two paid public API checks per configured provider."""

import argparse
import asyncio
import io
import math
import os
import struct
import wave
from collections.abc import Sequence
from pathlib import Path

from speech_sdk import AudioOutput, generate_speech, stream_speech

TEXT = "Hello from the HYBRD Python speech SDK. This is an end-to-end test."
ARTIFACTS = Path("artifacts/smoke")
MODELS = {"xai": "grok-tts", "openai": "gpt-4o-mini-tts"}
KEYS = {"xai": "XAI_API_KEY", "openai": "OPENAI_API_KEY"}
MAX_BYTES = 24000 * 2 * 60 + 65536


def select(provider: str | None) -> list[str]:
    candidates = [provider] if provider else list(MODELS)
    selected = []
    for name in candidates:
        if os.environ.get(KEYS[name], "").strip():
            selected.append(name)
        else:
            print(f"{name}: NOT RUN (key not set; set {KEYS[name]})")
    if not selected:
        raise ValueError("Set XAI_API_KEY or OPENAI_API_KEY for the selected provider")
    return selected


def validate_wav(data: bytes) -> float:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Empty or oversized WAV")
    buffer = io.BytesIO(data)
    with wave.open(buffer, "rb") as audio:
        properties = (
            audio.getcomptype(),
            audio.getnchannels(),
            audio.getsampwidth(),
            audio.getframerate(),
        )
        if properties != ("NONE", 1, 2, 24000):
            raise ValueError("Expected mono 16-bit PCM WAV at 24000 Hz")
        # wave leaves the buffer just after the data chunk's size field.
        declared_size = struct.unpack_from("<I", data, buffer.tell() - 4)[0]
        pcm = audio.readframes(24000 * 60 + 1)
        if declared_size != 0xFFFFFFFF and len(pcm) != declared_size:
            raise ValueError("Truncated WAV")
    duration = len(pcm) / (2 * 24000)
    if len(pcm) % 2 or not 0 < duration < 60:
        raise ValueError("Invalid PCM frame length or duration")
    if not any(sample[0] for sample in struct.iter_unpack("<h", pcm)):
        raise ValueError("Silent PCM")
    return duration


def validate_metadata(
    provider: str,
    actual_provider: str,
    model: str,
    media_type: str,
    input_chars: int,
    latency: float,
) -> None:
    if (actual_provider, model, media_type, input_chars) != (
        provider,
        MODELS[provider],
        "audio/wav",
        len(TEXT),
    ):
        raise ValueError("Unexpected response metadata")
    if not math.isfinite(latency) or latency < 0:
        raise ValueError("Invalid latency")


def clear_artifacts(provider: str) -> None:
    folder = ARTIFACTS / provider
    for mode in ("buffered", "streamed"):
        for suffix in (".wav", ".wav.part"):
            (folder / (mode + suffix)).unlink(missing_ok=True)


async def synthesize(provider: str, mode: str) -> tuple[bytes, float]:
    model = f"{provider}/{MODELS[provider]}"
    voice = "eve" if provider == "xai" else "alloy"
    options: dict[str, object] | None = {"language": "en"} if provider == "xai" else None
    if mode == "buffered":
        result = await generate_speech(
            model=model,
            text=TEXT,
            voice=voice,
            output=AudioOutput("wav", 24000),
            provider_options=options,
            max_retries=0,
            timeout=60.0,
        )
        latency = result.metadata.latency_ms
        validate_metadata(
            provider,
            result.provider,
            result.model,
            result.audio.media_type,
            result.metadata.input_chars,
            latency,
        )
        return result.audio.data, latency
    async with stream_speech(
        model=model,
        text=TEXT,
        voice=voice,
        output=AudioOutput("wav", 24000),
        provider_options=options,
        max_retries=0,
        timeout=60.0,
    ) as stream:
        latency = stream.metadata.setup_latency_ms
        validate_metadata(
            provider,
            stream.provider,
            stream.model,
            stream.media_type,
            stream.metadata.input_chars,
            latency,
        )
        data = bytearray()
        async for chunk in stream.audio:
            if len(data) + len(chunk) > MAX_BYTES:
                raise ValueError("Oversized WAV stream")
            data.extend(chunk)
        return bytes(data), latency


def save(path: Path, data: bytes) -> None:
    partial = path.with_suffix(".wav.part")
    try:
        partial.write_bytes(data)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


async def check(provider: str, mode: str) -> bool:
    path = ARTIFACTS / provider / f"{mode}.wav"
    try:
        async with asyncio.timeout(90):
            data, latency = await synthesize(provider, mode)
            duration = validate_wav(data)
            save(path, data)
        timing = "full-call" if mode == "buffered" else "setup"
        print(
            f"{provider}/{MODELS[provider]} {mode}: PASS {len(data)} bytes "
            f"{duration:.3f}s 24000Hz mono16 {timing}={latency:.1f}ms {path}"
        )
        return True
    except Exception:
        # Do not render exception details: even SDK IDs may contain untrusted data.
        print(f"{provider}/{MODELS[provider]} {mode}: FAIL (request/audio/file check)")
        return False


async def run_checks(providers: Sequence[str]) -> int:
    ready = []
    failed = False
    # Clear both modes before any request, including interrupted buffered checks.
    for provider in providers:
        try:
            clear_artifacts(provider)
            (ARTIFACTS / provider).mkdir(parents=True, exist_ok=True)
            ready.append(provider)
        except OSError:
            print(f"{provider}: FAIL (artifact cleanup/setup; no requests made)")
            failed = True
    for provider in ready:
        for mode in ("buffered", "streamed"):
            if not await check(provider, mode):
                failed = True
    return int(failed or not providers)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=tuple(MODELS))
    arguments_parsed = parser.parse_args(arguments)
    try:
        providers = select(arguments_parsed.provider)
    except ValueError:
        print("Configuration error: set XAI_API_KEY or OPENAI_API_KEY; no checks run")
        return 2
    return asyncio.run(run_checks(providers))


if __name__ == "__main__":
    raise SystemExit(main())
