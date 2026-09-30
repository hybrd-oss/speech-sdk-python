# Speech SDK for Python

An independent Python port of [Jellypod's Speech SDK](https://github.com/Jellypod-Inc/speech-sdk), starting with OpenAI and xAI over direct HTTP.

**Status: planning.** No runtime implementation or PyPI release exists yet. The first milestone is scoped in [specs/001-port-scope.md](specs/001-port-scope.md); full upstream behavioral parity is the long-term goal, not a current claim.

## First milestone

- Async buffered speech generation and context-managed audio streaming.
- OpenAI and xAI adapters using `httpx`, without official provider SDKs, a Node runtime, or a hosted proxy.
- Common audio results, provider/model resolution, API-key configuration, native output formats, structured errors, timeouts, cancellation, and bounded retries.
- Offline HTTP contract tests and opt-in live provider checks.

Python 3.11+ is the initial target. Intended import name: `speech_sdk`. Distribution name is not reserved; confirm availability before publishing.

## Proposed usage — not runnable yet

```python
import asyncio
from pathlib import Path

from speech_sdk import generate_speech, stream_speech


async def main():
    result = await generate_speech(
        model="openai/gpt-4o-mini-tts",
        text="Hello from Python!",
        voice="alloy",
    )
    Path("hello.mp3").write_bytes(result.audio.data)

    async with stream_speech(
        model="xai/grok-tts",
        text="Audio delivered as it arrives.",
        voice="your-voice-id",
    ) as stream:
        with Path("stream.mp3").open("wb") as file:
            async for chunk in stream.audio:
                file.write(chunk)


asyncio.run(main())
```

Set `OPENAI_API_KEY` or `XAI_API_KEY` for the selected provider. Keys go directly to that provider; never commit them. Examples and API names remain provisional until implementation.

## Upstream and license

Reference baseline: [`0e5a670324fb7be51a22708fe08bd7cc50f09f99`](https://github.com/Jellypod-Inc/speech-sdk/tree/0e5a670324fb7be51a22708fe08bd7cc50f09f99) (`@speech-sdk/core` 0.34.0).

Apache-2.0; see [LICENSE](LICENSE). Upstream attribution: Copyright 2026 Jellypod, Inc. This is an independent HYBRD OSS project, not an official Jellypod release or affiliated with Jellypod. The initial repository contains planning documentation, not translated SDK code.
