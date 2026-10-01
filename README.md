<div align="center">

# Speech SDK for Python

**Async text-to-speech for OpenAI, xAI and Azure OpenAI, straight from Python.**

[![Checks](https://github.com/hybrd-oss/speech-sdk-python/actions/workflows/checks.yml/badge.svg?branch=main&event=push)](https://github.com/hybrd-oss/speech-sdk-python/actions/workflows/checks.yml?query=branch%3Amain+event%3Apush)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue)](LICENSE)

**[Quick start](#quick-start)** · **[Providers](#providers)** · **[Public API](#public-api)** · **[Development](#development)** · **[Upstream](#upstream-and-license)**

</div>

The Jellypod team is cracked, and they made a [Speech SDK](https://github.com/Jellypod-Inc/speech-sdk) for TypeScript. But the Python folks should get to have some fun too.

So we did what anyone naturally does these days: pointed our agents to the repo and told them to port it to Python.

No provider SDK, Node runtime, hosted proxy, or audio decoder is needed.

----

Built on the original **[Speech SDK by Jellypod](https://github.com/Jellypod-Inc/speech-sdk)**.
An independent HYBRD OSS port, not an official Jellypod release or affiliated with Jellypod.


## Features

- **Three providers, one async API** — `generate_speech` returns buffered audio bytes from OpenAI, xAI or Azure OpenAI.
- **HTTP audio streaming** — `stream_speech` yields chunks inside an async context manager, with demand-driven reading and cleanup.
- **Native audio formats** — MP3, WAV and raw PCM through `AudioOutput`, plus provider-native options; no local transcoding.
- **Direct provider requests** — explicit/configured/environment keys, configurable base URLs and caller-owned HTTP clients.
- **Bounded retries and safe error summaries** — shared HTTP handling, network-phase timeouts and no retries after stream exposure.

## Status

**Status: milestone one complete.** Both providers' buffered/streaming APIs have offline coverage; real xAI buffered **and** streamed WAV checks **PASS** on `3adfb10` (2026-09-30). Saved audio is valid, nonsilent mono signed 16-bit PCM at 24 kHz: buffered **6.0701667 s**, streamed **6.0705 s**, at ignored `artifacts/smoke/xai/buffered.wav` and `streamed.wav`. Live OpenAI remains **UNVERIFIED** (key absent). The user listened to the buffered xAI demo and `tags-demo.wav` and said they “sound great”; transcript/STT verification has **not** been performed. STT remains deferred. No PyPI release or full upstream parity is claimed. See [validation evidence](specs/007-validation-and-xai-e2e.md) and [scope](specs/README.md).

Recorded milestone on `3adfb10`: **108 offline tests**, **13 fully offline hooks**, and **104 runtime tests against clean noneditable wheels on Python 3.11 and 3.14**. These are historical milestone counts, not the current generic-model suite count. Exact setup and provenance are recorded in the validation evidence; these are not live OpenAI or listening checks.

**Azure OpenAI: implemented with offline verification; live UNVERIFIED.** Current Azure source based on `44b1c71` plus uncommitted changes passed **134 offline tests** (**18 Azure**), **13 offline hooks**, and **130 runtime tests per clean noneditable wheel on Python 3.11 and 3.14** (2026-09-30). Independent QA/review passed; hook logs were independently inspected, not rerun by QA. One authorized buffered speech request using explicit candidate `gpt-4o-mini-tts` returned **HTTP 404**, safely classified as deployment unavailable; streaming stopped with **zero requests** and no audio artifact. The request reached Azure, but does not verify authorization or a working TTS deployment. This deployment-configuration blocker is not a failure to implement the SDK. See [offline evidence](specs/010-azure-openai-tts.md#offline-evidence) and [live attempt](specs/010-azure-openai-tts.md#live-attempt). Final independent documentation review (`speech-b6t.10`) **PASS**; no publication is claimed.

Python 3.11+; offline runtime checks target 3.11 and 3.14. `httpx` is the only direct runtime dependency.

## Quick start

### Clone, install, run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first, then clone `main` (not a published PyPI package). uv manages the Python interpreter and environment:

```sh
git clone https://github.com/hybrd-oss/speech-sdk-python.git
cd speech-sdk-python
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

## Providers

| Provider | Prefix | Environment key | Default model (convenience, not a whitelist) |
| --- | --- | --- | --- |
| [OpenAI](https://developers.openai.com/api/docs/guides/text-to-speech) | `openai` | `OPENAI_API_KEY` | `gpt-4o-mini-tts` |
| [xAI](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech) | `xai` | `XAI_API_KEY` | `grok-tts` (metadata only) |
| [Azure OpenAI](specs/010-azure-openai-tts.md) | `azure` | `AZURE_OPENAI_API_KEY` | None; your deployment name is required |

All three providers support buffered synthesis and HTTP response-body streaming. Nonblank model IDs are accepted; OpenAI/Azure validate availability, while xAI IDs do **not** select a different REST backend. See [model IDs and constants](#model-ids-and-constants) below.

### Azure OpenAI

This implemented adapter targets **Azure OpenAI**, not Azure AI Speech/SSML. It uses direct `httpx` JSON `POST /openai/v1/audio/speech?api-version=preview` with **`api-key` only**, not Entra or a legacy deployment-path API. Live synthesis remains **UNVERIFIED** after the [deployment-unavailable attempt](specs/010-azure-openai-tts.md#live-attempt); a working Responses API does not establish a TTS deployment. The current smoke runner still accepts only OpenAI/xAI.

Set your own API key explicitly in your local environment; never print or commit it. These are placeholders, not supplied credentials:

```sh
export AZURE_OPENAI_API_KEY="your-azure-openai-key"
export AZURE_OPENAI_BASE_URL="https://YOUR-RESOURCE-NAME.openai.azure.com"
export AZURE_OPENAI_DEPLOYMENT_NAME="your-actual-deployment-name"
# AZURE_OPENAI_ENDPOINT is an alternative if AZURE_OPENAI_BASE_URL is unset.
```

PowerShell uses `$env:AZURE_OPENAI_API_KEY`, `$env:AZURE_OPENAI_BASE_URL` and `$env:AZURE_OPENAI_DEPLOYMENT_NAME` assignments. Resource roots and `/openai/v1` API roots work. Explicit `base_url` wins over `AZURE_OPENAI_BASE_URL`, then `AZURE_OPENAI_ENDPOINT`; a present blank fails, not falls back. No `.env` loader, Azure/OpenAI SDK or additional dependency is needed; use the uv installation above.

Save this as `azure_example.py`, then run `uv run --frozen --no-dev python azure_example.py`: **one paid buffered request, with no retries**, requiring your actual speech deployment. The Azure feature PR is pending; use this checkout until merged, after which the normal `main` clone instructions apply.

```python
import asyncio
from pathlib import Path

from speech_sdk import AudioOutput, AzureOpenAIProvider, generate_speech


async def main() -> None:
    provider = AzureOpenAIProvider(api_version="preview")
    result = await generate_speech(
        model=provider.model(),  # Requires AZURE_OPENAI_DEPLOYMENT_NAME.
        text="Hello from Python!",
        voice="alloy",
        output=AudioOutput("wav", 24000),
        max_retries=0,
    )
    path = Path("artifacts/smoke/azure/hello.wav")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result.audio.data)


asyncio.run(main())
```

Equivalent selectors: `model="azure/your-actual-deployment-name"` or `provider.model("your-actual-deployment-name")`; the ID is sent verbatim in JSON `model`, never in the URL. Bare `azure` and `.model()` read `AZURE_OPENAI_DEPLOYMENT_NAME`; there is no fake default or deployment whitelist. Explicit public-call key > configured key > `AZURE_OPENAI_API_KEY` only. For streaming, reuse the [Public API](#public-api) `async with stream_speech(...)` example with that Azure selector, voice `alloy`, and no xAI `language` option; save to an ignored `artifacts/smoke/azure/` path. The same API and lifecycle apply. Native WAV/PCM uses 24 kHz with no local decoder/resampling; deployment aliases have unknown instruction capabilities, so valid instructions are forwarded even for aliases named `tts-1`/`tts-1-hd`. OpenAI still rejects instructions for those known unsupported model IDs. See [the strict contract](specs/010-azure-openai-tts.md#public-api-and-configuration) for validation/auth protection. Disclose AI-generated speech to users.

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

### Model IDs and constants

Model IDs accept any nonblank string, preserved verbatim; `provider/model-id` splits only on the first slash, so model namespaces work. Configured `.model("org/future-tts")` takes the ID directly without parsing a provider prefix. Unknown providers, blank IDs and invalid types still fail before HTTP.

Import `OPENAI_MODELS`, `XAI_MODELS`, `DEFAULT_OPENAI_MODEL` (`"gpt-4o-mini-tts"`) and `DEFAULT_XAI_MODEL` (`"grok-tts"`) from `speech_sdk`: these tuples/default strings are conveniences, **not whitelists**. Only OpenAI/xAI have these convenience constants; Azure has no default deployment constant. OpenAI/Azure send the exact caller ID; the provider validates availability and unknown-model/deployment capabilities.

**xAI IDs are metadata only:** its REST endpoint has no model selector, so a different ID does not select a different backend and native `model` options remain rejected.

### Formats and lifetime

Omitted output preserves native options or MP3 defaults. Common `AudioOutput` formats are MP3/WAV/raw PCM. OpenAI/Azure native options additionally support Opus/AAC/FLAC; xAI supports mulaw/alaw. No local transcoding/resampling is performed. OpenAI/Azure PCM is signed little-endian 16-bit at 24 kHz; xAI PCM has its validated native rate in the media type. OpenAI/Azure common output rates are fixed at 24 kHz; xAI supports 8/16/22.05/24/44.1/48 kHz, defaulting explicit WAV/PCM to 24 kHz. Omitted MP3 output does not invent sample-rate metadata.

`stream_speech` returns a context manager, **not an awaitable**. The audio iterator is single-pass, single-consumer and valid only inside the context, with demand-driven reading and no replay. Chunks need not align with PCM frames. Responses close on completion/early exit/errors/cancellation; SDK-owned HTTP clients close too, injected `http_client` instances remain caller-owned.

### Privacy and reliability

Keys, text, instructions, voice IDs and native options go directly to the chosen provider, subject to its policies; no HYBRD gateway is involved. Custom provider base URLs receive credentials too: trust the destination. Shared HTTP auth defaults to `Authorization: Bearer ...` for OpenAI/xAI; the [Azure contract](specs/010-azure-openai-tts.md#shared-authentication-protection) selects canonical `api-key` only. Caller headers from both auth families are stripped case-insensitively after removing hyphens/underscores (including compact aliases), then canonical auth and JSON content-type are set. Framing headers are rejected, redirects are not followed, and injected client defaults are not mutated. Do not log keys, request bodies, raw provider responses or error `model`/`details`/`raw_response` attributes. Error `str`/`repr` omit untrusted model IDs and raw provider codes/request IDs (only code/ID presence is shown); explicit attributes retain those values for caller inspection. Runner failures report the local phase, a static safe category, validated HTTP status when available and allowlisted network causes, never exception text, provider codes/IDs, headers, URLs or response details.

Default SDK `timeout=60.0` covers HTTPX network phases, not total synthesis time; use `asyncio.timeout(...)` for an overall deadline. Longer xAI inputs may need longer timeouts. Default `max_retries=2` means up to three attempts. Ambiguous failed POSTs may already be billed: retries can duplicate synthesis/cost; use `max_retries=0` to bound attempts. No idempotency guarantee is invented. Published streams never retry, reconnect or replay.

Text is sent verbatim; limits use Python Unicode code points (OpenAI/Azure 4096, xAI 60000). OpenAI instructions fail locally only for known unsupported `tts-1`/`tts-1-hd`; unknown model IDs forward instructions for provider validation. Azure forwards valid instructions for provider validation without inferring capabilities from deployment aliases. xAI instructions remain unsupported. SSE, timestamp envelopes, pronunciation replacement and unsupported options fail locally. The shared OpenAI/Azure body contract reserves auth/transport/protocol controls, including native `authorization`, `api_key`, `api_version`, `timestamps` and `with_timestamps`: presence is rejected even when the value is `False`. Case, compact, hyphen and underscore aliases are also rejected; recognized native fields require exact canonical names. Only other finite JSON-serializable options may be forwarded for provider validation; see [004](specs/004-openai-tts.md#request-contract). STT/alignment, cloning, long-text chunking, expressive-tag translation, conversations, sync facade, audio processing and publishing remain outside this milestone.

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
