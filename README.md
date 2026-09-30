# Speech SDK for Python

The Jellypod team is cracked, and they made a [Speech SDK](https://github.com/Jellypod-Inc/speech-sdk) for typescript. But the python folks should get to have some fun too. 

So we did what anyone naturally does these days: pointed our agents to the repo and told them to port it to python.

No provider SDK, Node runtime, hosted proxy, or audio decoder is needed.

-----

**Status: milestone one complete.** Both providers' buffered/streaming APIs have offline coverage; real xAI buffered **and** streamed WAV checks **PASS** on `3adfb10` (2026-09-30). Saved audio is valid, nonsilent mono signed 16-bit PCM at 24 kHz: buffered **6.0701667 s**, streamed **6.0705 s**, at ignored `artifacts/smoke/xai/buffered.wav` and `streamed.wav`. Live OpenAI remains **UNVERIFIED** (key absent), manual listening **NOT DONE**, and STT deferred. No PyPI release or full upstream parity is claimed. See [validation evidence](specs/007-validation-and-xai-e2e.md) and [scope](specs/README.md).

Verified offline: **108 tests**, **13 fully offline hooks**, and **104 runtime tests against clean noneditable wheels on Python 3.11 and 3.14**. Exact setup and provenance are recorded in the validation evidence; these are not live OpenAI or listening checks.

Python 3.11+; offline runtime checks target 3.11 and 3.14. `httpx` is the only direct runtime dependency. 

## Clone, install, run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first, then clone this branch (not a published PyPI package). uv manages the Python interpreter and environment:

```sh
git clone https://github.com/hybrd-oss/speech-sdk-python.git
cd speech-sdk-python
git checkout feat/two-provider-tts
uv sync --frozen --no-dev --python 3.11

# Set your own key locally; never commit it:
export XAI_API_KEY="your-xai-key"
# export OPENAI_API_KEY="your-openai-key"
# Paid: one buffered + one streamed request, no retries:
uv run --frozen --no-dev python smoke/run.py --provider xai
# Alternative: --provider openai (requires OPENAI_API_KEY)
```

Windows PowerShell equivalents:

```powershell
git clone https://github.com/hybrd-oss/speech-sdk-python.git
cd speech-sdk-python
git checkout feat/two-provider-tts
uv sync --frozen --no-dev --python 3.11
$env:XAI_API_KEY = "your-xai-key"
# $env:OPENAI_API_KEY = "your-openai-key"
# Paid: one buffered + one streamed request, no retries:
uv run --frozen --no-dev python smoke/run.py --provider xai
# Alternative: --provider openai (requires OPENAI_API_KEY)
```

**Explicit runner execution spends API credits.** The xAI command above runs exactly two short synthesis checks on success (one buffered, one streamed), bounded to at most two sequential requests with `max_retries=0`. Omitting `--provider` runs all configured providers; each selected provider gets at most two requests: fixed short text, `eve` (xAI, language `en`) or `alloy` (OpenAI), WAV at 24 kHz, no retries, 60-second network-phase timeouts and a 90-second overall deadline per check. Import, `--help`, offline tests and CI never synthesize speech. No voice/model discovery calls are made.

Missing keys fail preflight with exit **2**, before files or HTTP. Default mode visibly marks missing providers **NOT RUN**; explicit missing-provider selection is an error. All selected checks passing gives **0**; any ordinary request/audio/file failure gives **1** and remaining checks continue. Cancellation/Ctrl-C propagates nonzero. No `.env` loader is used.

Validated artifacts are saved only after complete responses at `artifacts/smoke/<provider>/buffered.wav` and `streamed.wav` (ignored). Old selected artifacts are cleared before requests; cleanup failure prevents that provider's requests. PCM must be uncompressed mono signed 16-bit at 24 kHz, positive duration under 60 seconds and non-silent. Actual PCM byte lengths are checked, including streaming sentinel headers. Timings are **buffered full-call** and **streaming setup**, not first-audio-byte latency. You may listen manually; there is no autoplay or STT. Non-silence does not prove intelligibility or the expected words.

**OpenAI applications must clearly disclose to users that the voice is AI-generated, not human**, including when presenting sample artifacts. See [OpenAI's TTS guide](https://developers.openai.com/api/docs/guides/text-to-speech).

## Public API

```python
import asyncio
from pathlib import Path

from speech_sdk import AudioOutput, generate_speech, stream_speech


async def main() -> None:
    result = await generate_speech(
        model="openai/gpt-4o-mini-tts",
        text="Hello from Python!",
        voice="alloy",
        max_retries=0,
    )
    Path("hello.mp3").write_bytes(result.audio.data)

    async with stream_speech(
        model="xai/grok-tts",
        text="Hello from Python!",
        voice="eve",
        output=AudioOutput("wav", 24000),
        provider_options={"language": "en"},
        max_retries=0,
    ) as stream:
        with Path("hello.wav").open("wb") as file:
            async for chunk in stream.audio:
                file.write(chunk)


asyncio.run(main())
```

These examples are paid calls, not smoke validation; their output files are caller-owned. Bare `openai`/`xai` select defaults. Configured `OpenAIProvider(...).model()` / `XAIProvider(...).model()` are also accepted. Explicit `api_key` overrides provider configuration then environment; explicit blank keys fail rather than fall back.

### Formats and lifetime

Omitted output preserves native options or MP3 defaults. Common `AudioOutput` formats are MP3/WAV/raw PCM. OpenAI native options additionally support Opus/AAC/FLAC; xAI supports mulaw/alaw. No local transcoding/resampling is performed. OpenAI PCM is signed little-endian 16-bit at 24 kHz; xAI PCM has its validated native rate in the media type. OpenAI common output rates are fixed at 24 kHz; xAI supports 8/16/22.05/24/44.1/48 kHz, defaulting explicit WAV/PCM to 24 kHz. Omitted MP3 output does not invent sample-rate metadata.

`stream_speech` returns a context manager, **not an awaitable**. The audio iterator is single-pass, single-consumer and valid only inside the context, with demand-driven reading and no replay. Chunks need not align with PCM frames. Responses close on completion/early exit/errors/cancellation; SDK-owned HTTP clients close too, injected `http_client` instances remain caller-owned.

### Privacy and reliability

Keys, text, instructions, voice IDs and native options go directly to the chosen provider, subject to its policies; no HYBRD gateway is involved. Custom provider base URLs receive credentials too: trust the destination. Protected authorization/content-type headers cannot be overridden, framing headers are rejected, redirects are not followed, and injected client defaults are not mutated. Do not log keys, request bodies, raw provider responses or error `details`/`raw_response` attributes. Runner failures report the local phase, a static safe category, validated HTTP status when available and allowlisted network causes, never exception text, provider codes/IDs, headers, URLs or response details.

Default SDK `timeout=60.0` covers HTTPX network phases, not total synthesis time; use `asyncio.timeout(...)` for an overall deadline. Longer xAI inputs may need longer timeouts. Default `max_retries=2` means up to three attempts. Ambiguous failed POSTs may already be billed: retries can duplicate synthesis/cost; use `max_retries=0` to bound attempts. No idempotency guarantee is invented. Published streams never retry, reconnect or replay.

Text is sent verbatim; limits use Python Unicode code points (OpenAI 4096, xAI 60000). Instructions are supported only on `gpt-4o-mini-tts`; SSE, timestamp envelopes, pronunciation replacement and unsupported options fail locally. STT/alignment, cloning, long-text chunking, expressive-tag translation, conversations, sync facade, audio processing and publishing remain outside this milestone.

## Development

```sh
uv sync --frozen --python 3.11
uv run --frozen prek install
uv run --frozen prek run --all-files
```

Strict mypy, Ruff, Vulture, Radon <=10, local Semgrep and offline unittest checks require no provider keys. See [CONTRIBUTING.md](CONTRIBUTING.md) for runtime-only and clean-wheel checks.

## Upstream and license

Reference baseline: [`0e5a670324fb7be51a22708fe08bd7cc50f09f99`](https://github.com/Jellypod-Inc/speech-sdk/tree/0e5a670324fb7be51a22708fe08bd7cc50f09f99) (`@speech-sdk/core` 0.34.0).

Apache-2.0; see [LICENSE](LICENSE). Upstream attribution: Copyright 2026 Jellypod, Inc. This is an independent HYBRD OSS project, not an official Jellypod release or affiliated with Jellypod. Behavioral differences and deferred parity are documented in [specs](specs/README.md).
