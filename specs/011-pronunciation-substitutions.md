# 011 — Pronunciation substitutions

Status: implemented. Original substitution acceptance **PASS**; independent QA (`speech-488.3`) and source-parity review (`speech-488.4`) are CLOSED/PASS for that original implementation (historical evidence below).

## Scope and provenance

Lexical respelling for the existing OpenAI, xAI and Azure OpenAI buffered/streamed APIs; no IPA interpretation.
Adapt Jellypod-Inc/speech-sdk 0.34.0 at [`0e5a670324fb7be51a22708fe08bd7cc50f09f99`](https://github.com/Jellypod-Inc/speech-sdk/tree/0e5a670324fb7be51a22708fe08bd7cc50f09f99).
Reference: `src/pronunciations/{types,merge,substitute}.ts` and `src/__tests__/pronunciations-{merge,substitute,generate-speech,stream-speech}.test.ts` at that pin.
The implementation module credits Jellypod, names this pin and identifies Python modifications; existing license/distribution attribution is preserved.
Build on [002](002-api-and-package.md), [003](003-http-and-errors.md), [004](004-openai-tts.md), [005](005-xai-tts.md), [006](006-http-streaming.md) and [010](010-azure-openai-tts.md).

## Public types and helpers

Use one `speech_sdk.pronunciations` module, standard library only; its public contract is:
```python
@dataclass(frozen=True)
class Pronunciation:
    word: str
    replacement: str
    case_sensitive: bool = False

@dataclass(frozen=True)
class Edit:
    original_range: tuple[int, int]
    replacement_range: tuple[int, int]
    original_word: str
    rule_key: str

@dataclass(frozen=True)
class SubstitutionResult:
    text: str
    edits: tuple[Edit, ...]

merge_rules(rules: Sequence[Pronunciation]) -> dict[str, Pronunciation]
rule_map_key(word: str, case_sensitive: bool) -> str

class PronunciationMatcher:
    def __init__(self, rule_map: Mapping[str, Pronunciation]) -> None: ...
    def substitute(self, text: str) -> SubstitutionResult: ...

substitute(text: str, rule_map: Mapping[str, Pronunciation]) -> SubstitutionResult
```
Add only `Pronunciation` to package-root imports/`__all__`; import the other new names from `speech_sdk.pronunciations`.
`Pronunciation.__post_init__` requires exact built-in `str` fields and exact `bool`; accept blank strings here. Helpers revalidate fields and require `type(rule) is Pronunciation`, rejecting subclasses without invoking their overrides. No coercion or dictionary rules.
`merge_rules` and non-`None` SDK input require `isinstance(rules, collections.abc.Sequence)`, excluding `str`, `bytes`, `bytearray` and `Mapping`; list/tuple and ordinary finite sequences work, generators/iterators/sets do not. Snapshot with `tuple(rules)` before any rule validation/processing. These are trusted local containers, not network frames; no custom-method security framework.
`merge_rules` strips ends with Python `str.strip()` only, preserves internal whitespace, skips rules whose stripped word or replacement is blank, and returns new normalized frozen rules without mutating input. Python Unicode whitespace rules apply, not an emulated JavaScript trim table.
`rule_map_key` validates exact built-in `str`/`bool`, does not trim, and returns the word unchanged if sensitive, otherwise `word.lower()`.
Duplicate canonical keys are last-write-wins, retaining the key's original insertion position. A lowercase sensitive word can collide with its insensitive variant; uppercase sensitive/insensitive variants can coexist, as upstream.
`PronunciationMatcher` requires a `Mapping`, snapshots its values before validation, then clones the validated frozen rules and compiles them once for reuse across input strings. Later caller map/rule changes do not affect the matcher; no global cache or lock is used. Accept valid manually assembled maps: values must be exact validated `Pronunciation` with already stripped, nonblank word/replacement; otherwise raise `ValueError` for blank/unnormalized values, `TypeError` for invalid types. Never silently admit zero-length matches.
`PronunciationMatcher.substitute` requires exact built-in `str` text (even for an empty map) and accepts empty/whitespace text. The compatible free `substitute(text, rule_map)` validates text first, then constructs a matcher for that one call; `merge_rules` and `rule_map_key` remain unchanged.
External map keys are ignored, not validated or copied into edits: compute each matched `rule_key` with `rule_map_key`. Preserve values' insertion order, including canonical-key collisions in manual maps; do not remerge them. No caller map mutation.
All new validation errors are static and omit input text, words, replacements, map keys and IDs; no automatic logging.

## Matching and edits

Compile dictionary buckets by original `len(word)`, with separate exact and lowercase keys and map-insertion ranks. Keep the first candidate per key in each dictionary; choose the lowest rank across exact/insensitive hits to preserve equal-length ties, including manual-map collisions. Sort distinct lengths descending. At each original-text boundary, try one fixed slice per eligible length, require a boundary at its end, and stop at the first matching length; no per-rule scan.
A boundary is either text edge or a gap whose adjacent characters are not both word characters; word characters are `char.isalnum()` or `_` (Unicode letters/numbers, including nondecimal numbers). No regex syntax or ASCII-only boundary approximation.
Look up the fixed-original-length slice literally in the exact dictionary and `slice.lower()` in the insensitive dictionary. Lower each candidate slice, not the whole text: Unicode expansion and context-dependent Greek sigma must retain the original offsets/matching context. Use `.lower()`, never `.casefold()`; `ß` does not match `ss`. Preserve original text/replacement spelling otherwise.
Scan left-to-right once: append a match's replacement and advance by the original word length, or append one original character and advance one. Never rescan replacement text, chain rules or recurse.
Each edit records half-open original and output ranges, the exact original slice (including its case), and the computed canonical rule key. Output offsets use accumulated output length, correctly accounting for expansion/contraction and preceding edits.
Ranges/lengths count Python Unicode code points, intentionally not upstream UTF-16 units or grapheme clusters; emoji/combining text is not grapheme-normalized. Result edits and both range pairs are immutable tuples. No matches returns unchanged text and `edits=()`.
With `R` rules, `L` distinct original word lengths and `N` input code points, compilation costs `O(total rule characters + R + L log L)`; scanning costs `O(N * sum(distinct lengths) + output size)`. Dictionary lookup itself is expected constant time, but slicing, lowering and hashing cost string length. Same-length rules do not add scan work after compilation; many distinct lengths remain a ceiling, not an unconditional linear-time guarantee. The `ponytail:` comment reserves a Unicode-aware trie for a measured many-length bottleneck; no trie, factory, dependency or alignment abstraction now.

## Synthesis integration

Both public functions retain their existing keyword-only shape and identical parameter annotations/defaults. `pronunciations: Sequence[Pronunciation] | None = None` sits immediately after `instructions`, before `provider_options`; it still accepts a rule sequence, not a `PronunciationMatcher`:
```python
# async def generate_speech(...) -> SpeechResult
# def stream_speech(...) -> AbstractAsyncContextManager[SpeechStream]
(*, model: str | ResolvedModel, text: str, voice: str,
 output: AudioOutput | None = None, instructions: str | None = None,
 pronunciations: Sequence[Pronunciation] | None = None,
 provider_options: Mapping[str, object] | None = None, api_key: str | None = None,
 http_client: httpx.AsyncClient | None = None, timeout: float | httpx.Timeout = 60.0,
 max_retries: int = 2, headers: Mapping[str, str] | None = None)
```
After model resolution, before provider `prepare`, use one shared pronunciation path in both APIs: snapshot/validate rules, require plain built-in text before calling its methods or `len`, merge and substitute. Stream processing remains on context entry, not eager HTTP/client creation at `stream_speech()` invocation.
When `pronunciations is None`, preserve the existing path and validation, including existing acceptance of text subclasses; do not broadly tighten unrelated API validation. Empty/all-blank rules produce no text edits (the explicit pronunciation path still validates plain text).
Provider `prepare` signatures and direct calls stay unchanged. Adapters validate final provider-visible text: 4,096 code points OpenAI/Azure, 60,000 xAI. Oversized original input may contract below the limit and succeed; expansion beyond the final limit fails locally. Empty/whitespace SDK text still raises existing local `NoSpeechGeneratedError` and cannot become speech through a blank-word rule.
Both metadata types' existing `input_chars` remain `len(caller_original_text)`, not the transformed length; preserve that count centrally without adding metadata fields. Transformation/validation precedes SDK-owned client creation and any request; retries reuse prepared transformed bytes, never apply substitutions again.
Only canonical synthesis text changes (`input` OpenAI/Azure, `text` xAI). Preserve canonical model/voice precedence, instruction combination, native options/output, authentication, timeout/retry rules and caller ownership/mappings. Audio bytes, demand-driven streaming, cleanup and no-replay behavior remain unchanged.
No tag preprocessing: preserve existing tag text unless an explicit literal rule matches it (including inside tags). Unlike upstream's tag-stripping integration, no provider-aware tag processing is included. No inverse alignment, timestamps/STT, cloning, additional providers/adapters, audio operations or hidden parser/codec limitations are introduced.

## Offline acceptance

One focused `tests/test_pronunciations.py` uses upstream-derived table cases: empty/trimmed/blank rules, duplicate/colliding keys, stable equal-length order, real phrase overlap (`New York` vs `New`), exact case, Unicode letters/numbers/underscores, emoji offsets, literal `[bracket]`/`C++`, multiline/internal whitespace and `.lower()` expansion without casefolding. Assert exact text, normalized maps and immutable edit tuples/ranges, expansion/contraction, manual-map canonical keys and no chaining.
Check invalid fields/bools, rule/text subclasses, dictionaries/string-like containers/generators, malformed manual maps and static diagnostics; prove snapshot behavior and no mutation. For the reusable matcher, cover rule cloning, repeated inputs, exact/lowercase rank collisions, original-length Unicode expansion/contextual lowering and class/helper equivalence. A deterministic lookup-count check must show fixed scan work for 100 and 1000 same-length rules, without elapsed-time thresholds. Pure empty text succeeds; SDK blank text fails before owned-client creation with zero requests.
Use `httpx.MockTransport` for all three existing providers in both modes: exact transformed requests with unchanged voice/instructions/options/auth/output, original metadata for expanding/shrinking inputs, final limit `N`/`N+1` and oversized-raw contraction. Cover `None`/empty/all-blank no-ops, unchanged tags unless literally matched, and 429→success with identical once-transformed bodies. One demand-driven stream integration check plus existing lifecycle/no-replay regressions suffices.
Update only the expected public parameter list to add `pronunciations`, preserve baseline regressions, and include the focused module in existing offline runtime/clean-wheel checks. No new dependency/tool pin or paid/default live call; no Node execution/vendor SDK.

## Verified offline evidence

Indexed OOP refactor (`speech-e5w`): implementation self-verification recorded **21 focused**, **155 checkout** tests and **13 offline hooks** PASS in credential-free Python 3.11 with OS network denial. These are implementation checks, not independent QA or new wheel evidence.

**Historical original substitution evidence (before the indexed OOP refactor):**

- Tested checkout: `feat/pronunciation-substitutions`, base `0151e36` plus feature changes; implementation/test-fix logs record **148 checkout**, **14 focused**, **144 runtime-selection** tests PASS and **13 offline hooks** PASS.
- Independent QA reran **148 checkout** and **14 focused** tests PASS after the equal-length Greek sigma golden fix; all **17 in-memory mutants killed**, including `.lower()` → `.casefold()`, without disk source mutation.
- Independent review read pinned upstream/source/tests and reran **14 focused** tests PASS; source/API contract correct, no outstanding QA/review blocker. Hook results were inspected by QA/review, not independently rerun there.
- Checks used the existing Python 3.11 venv, credential-free environments, `UV_OFFLINE=1` and OS network denial; pure helpers/MockTransport only. No live synthesis or listening is claimed.
- Fresh post-documentation noneditable wheels passed **144 runtime-selection tests each on Python 3.11 and 3.14** in clean, hash-locked installs with credentials unset and OS network denial (`/tmp/speech-sdk-pronunciation-wheel.UMvcvK/tests-3.11.log` and `tests-3.14.log`); final documentation review (`speech-488.8`) is CLOSED/PASS. Historical 007 and Azure 010 counts/provenance remain unchanged.
