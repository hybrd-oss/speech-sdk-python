# Implementation specs

Status: **milestone one complete.** 002–006 adapters/public APIs, 008 smoke runner and 009 baseline are implemented. [007 live gate](007-validation-and-xai-e2e.md): xAI buffered **and** streamed WAV **PASS** on `3adfb10` (2026-09-30); valid, nonsilent mono signed 16-bit PCM at 24 kHz, **6.0701667 s** buffered and **6.0705 s** streamed, saved to ignored `artifacts/smoke/xai/buffered.wav` and `streamed.wav`. Historical failures remain in 007, not current status.

Verified: **108 offline tests**, **13 fully offline hooks**, **104 runtime tests against clean noneditable wheels on Python 3.11/3.14**; exact environment/provenance is in 007. OpenAI has offline coverage but is **live UNVERIFIED** (key absent); manual listening **NOT DONE**, STT deferred. No PyPI release or full upstream parity is claimed. Start with [001: scope](001-port-scope.md).

| Spec | Work | Depends on |
| --- | --- | --- |
| [009](009-quality-baseline.md) | Strict tooling, prek hooks, local security rules and public CI — implemented first | — |
| [002](002-api-and-package.md) | Python package, public API, results, provider/model resolution | — |
| [003](003-http-and-errors.md) | HTTP lifecycle, errors, retry policy, timeouts | 002 |
| [004](004-openai-tts.md) | OpenAI buffered TTS and request contract | 002, 003 |
| [005](005-xai-tts.md) | xAI buffered TTS and request contract | 002, 003 |
| [006](006-http-streaming.md) | Streaming for both providers, backpressure and cleanup | 003, 004, 005 |
| [007](007-validation-and-xai-e2e.md) | Offline coverage, live xAI completion gate, package/CI checks | 004, 005, 006, 008 |
| [008](008-clone-and-run-smoke-tests.md) | Clone-and-run smoke suite for anyone with provider keys | 004, 005, 006 |

Implementation order:

```text
009 baseline (done) → 002 shared contracts → 003 transport/errors
                         ├─ 004 OpenAI ─┐
                         └─ 005 xAI ────┴→ 006 streaming → 008 smoke suite → 007 live xAI gate
```

Each implementation ships its own offline checks; 007 integrates and verifies them, rather than postponing testing to the end. Specs are work boundaries, not a requirement for one class/module per spec.

## Completion gate

Milestone one's gate is **satisfied**: offline suite/package checks and a real xAI E2E passed through the public API for both buffered and streamed WAV. The clone-and-run smoke suite covers both providers; its explicit xAI run is the E2E gate. Future missing-key/skipped runs are not passing evidence and do not replace the recorded result. Live OpenAI verification remains desirable but is not the minimum completion gate.

## Decisions and intentional differences

- Python 3.11+, async-first, `httpx` as the only initial runtime dependency; standard-library `unittest` checks.
- Binary HTTP responses only. No provider SDKs, WebSocket transport, Node runtime, or audio-processing dependency.
- Current xAI docs specify 60,000 input characters; pinned upstream uses 15,000. Use 60,000 and record this as a provider-contract update, not upstream parity.
- xAI's normal wire default is 24 kHz. Pinned upstream's output helper selects 48 kHz for explicit WAV/PCM without a rate. Initial Python output defaults to 24 kHz; explicit rates remain configurable.
- Input text is passed verbatim. Upstream OpenAI expressive-tag extraction, pronunciation mapping, chunking, and alignment remain deferred.
- Header/setup latency is not first audio-byte latency. Expose truthful timing fields instead of copying upstream's misleading streaming `ttfbMs` label.
- Do not retry arbitrary Python exceptions. Retry only classified provider/network/empty-audio failures.
- User-configured keys and core request fields cannot be silently replaced by custom headers/options.

## Sources

Upstream reference: [`0e5a670324fb7be51a22708fe08bd7cc50f09f99`](https://github.com/Jellypod-Inc/speech-sdk/tree/0e5a670324fb7be51a22708fe08bd7cc50f09f99).

Provider documentation reviewed on **2026-09-30**; recheck before changing contracts. Required live xAI WAV checks passed; other codecs/rates/options are not exhaustively live-verified. Specific references and remaining verification items live in the provider specs.

Broader provider/audio/conversation parity stays in [001](001-port-scope.md). Do not scaffold that work during milestone one.
