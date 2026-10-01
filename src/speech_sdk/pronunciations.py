"""Literal pronunciation substitutions adapted from Jellypod-Inc/speech-sdk (Apache-2.0).

Source pin: 0e5a670324fb7be51a22708fe08bd7cc50f09f99, pronunciations/types,
merge and substitute. Python modifications: frozen typed dataclasses, explicit
runtime validation and snapshots, Python strip/lower/isalnum semantics, immutable
edits and Unicode code-point offsets instead of JavaScript UTF-16 offsets.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "Edit",
    "Pronunciation",
    "SubstitutionResult",
    "merge_rules",
    "rule_map_key",
    "substitute",
]


@dataclass(frozen=True)
class Pronunciation:
    word: str
    replacement: str
    case_sensitive: bool = False

    def __post_init__(self) -> None:
        _validate_fields(self.word, self.replacement, self.case_sensitive)


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


def _validate_fields(word: str, replacement: str, case_sensitive: bool) -> None:
    if type(word) is not str or type(replacement) is not str:
        raise TypeError("Pronunciation word and replacement must be plain strings")
    if type(case_sensitive) is not bool:
        raise TypeError("Pronunciation case_sensitive must be a boolean")


def _validate_rule(rule: Pronunciation) -> None:
    if type(rule) is not Pronunciation:
        raise TypeError("Rules must be Pronunciation instances")
    _validate_fields(rule.word, rule.replacement, rule.case_sensitive)


def rule_map_key(word: str, case_sensitive: bool) -> str:
    _validate_fields(word, "", case_sensitive)
    return word if case_sensitive else word.lower()


def merge_rules(rules: Sequence[Pronunciation]) -> dict[str, Pronunciation]:
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes, bytearray, Mapping)):
        raise TypeError("Pronunciations must be a finite sequence of rules")
    snapshot = tuple(rules)
    merged: dict[str, Pronunciation] = {}
    for rule in snapshot:
        _validate_rule(rule)
        word, replacement = rule.word.strip(), rule.replacement.strip()
        if word and replacement:
            normalized = Pronunciation(word, replacement, rule.case_sensitive)
            merged[rule_map_key(word, rule.case_sensitive)] = normalized
    return merged


def _sorted_rules(rule_map: Mapping[str, Pronunciation]) -> list[Pronunciation]:
    if not isinstance(rule_map, Mapping):
        raise TypeError("Rule map must be a mapping")
    snapshot = tuple(rule_map.values())
    for rule in snapshot:
        _validate_rule(rule)
        if not rule.word or not rule.replacement:
            raise ValueError("Rule map values must be nonblank and normalized")
        if rule.word != rule.word.strip() or rule.replacement != rule.replacement.strip():
            raise ValueError("Rule map values must be nonblank and normalized")
    return sorted(snapshot, key=lambda rule: len(rule.word), reverse=True)


def _word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _boundary(text: str, index: int) -> bool:
    return (
        index == 0
        or index == len(text)
        or not (_word_char(text[index - 1]) and _word_char(text[index]))
    )


def _match(text: str, index: int, rules: Sequence[Pronunciation]) -> Pronunciation | None:
    if not _boundary(text, index):
        return None
    for rule in rules:
        end = index + len(rule.word)
        if end > len(text) or not _boundary(text, end):
            continue
        literal = text[index:end]
        matches = (
            literal == rule.word if rule.case_sensitive else literal.lower() == rule.word.lower()
        )
        if matches:
            return rule
    return None


def substitute(text: str, rule_map: Mapping[str, Pronunciation]) -> SubstitutionResult:
    if type(text) is not str:
        raise TypeError("Pronunciation text must be a plain string")
    rules = _sorted_rules(rule_map)
    if not rules:
        return SubstitutionResult(text, ())
    out: list[str] = []
    edits: list[Edit] = []
    index = output_length = 0
    # ponytail: O(text * rules * word_length); index matching only if measured throughput needs it.
    while index < len(text):
        rule = _match(text, index, rules)
        end = index + len(rule.word) if rule else index + 1
        replacement = rule.replacement if rule else text[index]
        out.append(replacement)
        if rule:
            edits.append(
                Edit(
                    (index, end),
                    (output_length, output_length + len(replacement)),
                    text[index:end],
                    rule_map_key(rule.word, rule.case_sensitive),
                )
            )
        output_length += len(replacement)
        index = end
    return SubstitutionResult("".join(out), tuple(edits))
