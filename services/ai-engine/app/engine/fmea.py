"""Master FMEA matching (spec §8, §12).

Four levels, tried in the fixed order of spec §8.1, stopping at the first that
yields anything:

    EXACT -> NORMALIZED -> SEMANTIC -> LLM_CANDIDATE -> NO_MATCH

`NO_MATCH` is terminal and returns *no* records. The one thing this engine must
never do is dress a miss up as a plausible historical FMEA row (spec §8.2): an
empty candidate list plus `requiresHumanReview` is the honest answer, and the
caller routes it to a person.

RPN and AP are derived from S/O/D at load time, never stored (spec §6.1) — the
file cannot disagree with the arithmetic, and retuning the AP thresholds
re-scores every row on the next load.
"""

from __future__ import annotations

import json
import os
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.config import DEFAULT_MASTER_FMEA_PATH, AssessRules
from app.domain.models import (
    FmeaCandidate,
    FmeaMatchResult,
    FmeaMatchStatus,
    FmeaRecord,
    FourMOneECategory,
)
from app.engine.risk import compute_action_priority, compute_rpn

CandidateGenerator = Callable[["FmeaQuery", Sequence[FmeaRecord]], Sequence[FmeaCandidate]]

# Relative importance of each field when scoring a fuzzy match. The failure mode
# is what the anomaly actually names; process and operation only disambiguate.
_FIELD_WEIGHTS = {"failure_mode": 0.6, "process": 0.2, "operation": 0.2}

# Punctuation is noise for matching: "扭矩不合格率上升" and "扭矩不合格率上升。" are
# the same failure mode. Whitespace is handled separately.
_PUNCTUATION = set("，。、；：,.;:!！?？()（）[]【】{}<>《》\"'`~-—_/\\|+*#&@$%^…·")


@dataclass(frozen=True, slots=True)
class FmeaQuery:
    """What we are looking up. `process`/`operation` are optional context."""

    failure_mode: str
    process: str | None = None
    operation: str | None = None


def load_master_fmea(rules: AssessRules, path: Path | None = None) -> tuple[FmeaRecord, ...]:
    """Read the synthetic Master FMEA and derive RPN/AP deterministically.

    Like the other config loaders, a missing or malformed file fails loudly: a
    silently empty FMEA would make every match a `NO_MATCH` and hide the cause.
    """
    fmea_path = path or Path(os.getenv("MASTER_FMEA_PATH") or DEFAULT_MASTER_FMEA_PATH)
    raw = json.loads(fmea_path.read_text(encoding="utf-8"))

    records: list[FmeaRecord] = []
    for item in raw["records"]:
        severity = int(item["severity"])
        occurrence = int(item["occurrence"])
        detection = int(item["detection"])
        category = item.get("category")
        records.append(
            FmeaRecord(
                process=str(item["process"]),
                operation=str(item["operation"]),
                failure_mode=str(item["failureMode"]),
                effect=str(item["effect"]),
                cause=str(item["cause"]),
                prevention_control=item.get("preventionControl"),
                detection_control=item.get("detectionControl"),
                severity=severity,
                occurrence=occurrence,
                detection=detection,
                rpn=compute_rpn(severity, occurrence, detection),
                ap=compute_action_priority(
                    severity, occurrence, detection, rules.action_priority
                ),
                recommended_action=item.get("recommendedAction"),
                category=FourMOneECategory(category) if category else None,
            )
        )
    return tuple(records)


def match_fmea(
    query: FmeaQuery,
    records: Sequence[FmeaRecord],
    rules: AssessRules,
    *,
    candidate_generator: CandidateGenerator | None = None,
) -> FmeaMatchResult:
    """Match `query` against the Master FMEA by descending precedence (spec §8.1).

    `candidate_generator` is the LLM-candidate hook. It is optional and off by
    default: the deterministic levels come first, and a caller that wants the
    LLM level must supply it explicitly. Whatever it returns is marked
    `LLM_CANDIDATE` and `requires_human_review`, so a generated cause is never
    confused with an established record.
    """
    max_candidates = rules.matching.max_candidates

    exact = [record for record in records if _is_exact(query, record)]
    if exact:
        return _from_records(
            FmeaMatchStatus.EXACT, exact, rules, max_candidates, review=False
        )

    normalized = [record for record in records if _is_normalized(query, record)]
    if normalized:
        return _from_records(
            FmeaMatchStatus.NORMALIZED, normalized, rules, max_candidates, review=False
        )

    scored = [
        (score, record)
        for record in records
        if (score := _semantic_score(query, record)) >= rules.matching.semantic_threshold
    ]
    if scored:
        scored.sort(key=lambda item: (-item[0], item[1].cause, item[1].failure_mode))
        candidates = [
            FmeaCandidate(
                record=record,
                score=round(score, 4),
                match_type=FmeaMatchStatus.SEMANTIC,
            )
            for score, record in scored[:max_candidates]
        ]
        return FmeaMatchResult(
            match_status=FmeaMatchStatus.SEMANTIC,
            requires_human_review=True,
            candidates=candidates,
        )

    if candidate_generator is not None:
        generated = list(candidate_generator(query, records))
        if generated:
            return FmeaMatchResult(
                match_status=FmeaMatchStatus.LLM_CANDIDATE,
                requires_human_review=True,
                candidates=generated[:max_candidates],
            )

    return FmeaMatchResult(
        match_status=FmeaMatchStatus.NO_MATCH,
        requires_human_review=True,
        candidates=[],
    )


# --------------------------------------------------------------------------- #
# Levels
# --------------------------------------------------------------------------- #


def _from_records(
    status: FmeaMatchStatus,
    records: Sequence[FmeaRecord],
    rules: AssessRules,
    max_candidates: int,
    *,
    review: bool,
) -> FmeaMatchResult:
    score = rules.match_scores[status.value]
    ordered = sorted(records, key=lambda record: (record.cause, record.failure_mode))
    candidates = [
        FmeaCandidate(record=record, score=score, match_type=status)
        for record in ordered[:max_candidates]
    ]
    return FmeaMatchResult(
        match_status=status, requires_human_review=review, candidates=candidates
    )


def _fields(query: FmeaQuery, record: FmeaRecord) -> list[tuple[str, str, float]]:
    """The (query, record, weight) triples the level tests compare.

    Only fields the caller actually supplied are compared — an unset process must
    not silently disqualify an otherwise exact failure-mode hit. When the failure
    mode is the sole field, it carries the full weight.
    """
    fields: list[tuple[str, str, float]] = [
        (query.failure_mode, record.failure_mode, _FIELD_WEIGHTS["failure_mode"])
    ]
    if query.process is not None:
        fields.append((query.process, record.process, _FIELD_WEIGHTS["process"]))
    if query.operation is not None:
        fields.append((query.operation, record.operation, _FIELD_WEIGHTS["operation"]))
    if len(fields) == 1:
        return [(query.failure_mode, record.failure_mode, 1.0)]
    return fields


def _is_exact(query: FmeaQuery, record: FmeaRecord) -> bool:
    return all(left.strip() == right.strip() for left, right, _ in _fields(query, record))


def _is_normalized(query: FmeaQuery, record: FmeaRecord) -> bool:
    return all(
        _normalize(left) == _normalize(right) for left, right, _ in _fields(query, record)
    )


def _semantic_score(query: FmeaQuery, record: FmeaRecord) -> float:
    fields = _fields(query, record)
    total = sum(weight for _, _, weight in fields)
    if total <= 0.0:
        return 0.0
    weighted = sum(
        _similarity(_normalize(left), _normalize(right)) * weight
        for left, right, weight in fields
    )
    return weighted / total


# --------------------------------------------------------------------------- #
# Text comparison
# --------------------------------------------------------------------------- #


def _normalize(text: str) -> str:
    """Fold case, width and punctuation, then drop whitespace.

    NFKC maps full-width Latin/digits onto their half-width forms, so a failure
    mode typed with a full-width space or digit still lands on the same record.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        char for char in folded if not char.isspace() and char not in _PUNCTUATION
    )


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[index : index + 2] for index in range(len(text) - 1)}


def _similarity(left: str, right: str) -> float:
    """Dice coefficient over character bigrams — 0–1, symmetric, deterministic.

    Bigrams (not words) because the failure modes are short Chinese phrases with
    no reliable whitespace: "一次合格率降低" vs "一次合格率下降" shares four of its
    six bigrams, which is the signal we want, while unrelated modes share almost
    none.
    """
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_grams = _bigrams(left)
    right_grams = _bigrams(right)
    denominator = len(left_grams) + len(right_grams)
    if denominator == 0:
        return 0.0
    return 2.0 * len(left_grams & right_grams) / denominator