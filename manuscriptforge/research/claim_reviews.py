"""Explicit, fingerprint-bound human decisions over immutable extracted claims.

The ledger records reviewer judgment, not proof of scientific validity. Applying
it creates copies; it never rewrites source claims, evidence, or earlier reviews.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from manuscriptforge.evidence.strength_classifier import (
    allowed_language_for_support,
    forbidden_language_for_support,
)
from manuscriptforge.models.claim import ScientificClaim, SupportStrength
from manuscriptforge.orchestration.storage import exclusive_lock, is_link
from manuscriptforge.utils.io import write_text

CLAIM_REVIEW_FILENAME = "claim_reviews.jsonl"
MAX_LEDGER_BYTES = 32 * 1024 * 1024
ReviewDecision = Literal["approve", "reject"]


class ClaimReviewLedgerError(ValueError):
    """A ledger cannot be trusted or an explicit decision cannot be recorded."""


class ClaimFingerprints(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ClaimReview(ClaimFingerprints):
    schema_version: Literal[1] = 1
    review_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    reviewed_at: datetime
    claim_id: str = Field(min_length=1, max_length=300)
    reviewer: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=4000)
    decision: ReviewDecision
    support_strength: SupportStrength
    section_target: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("Review schema version must be an integer")
        return value

    @field_validator("claim_id", "reviewer", "reason", "section_target")
    @classmethod
    def explicit_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Review text must be nonempty and contain no NUL")
        return value

    @field_validator("reviewed_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Review timestamp requires a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def explicit_support(self) -> ClaimReview:
        if self.decision == "approve" and self.support_strength not in {"direct", "indirect"}:
            raise ValueError("Approval requires explicitly assessed direct or indirect support")
        if self.decision == "reject":
            if self.support_strength != "unsupported":
                raise ValueError("A rejection must have unsupported strength")
            if self.section_target is not None:
                raise ValueError("A rejected claim cannot be routed to a section")
        return self


class ClaimReviewOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    status: Literal[
        "unreviewed", "approved", "rejected", "stale", "invalid_route", "invalid_review"
    ]
    review_id: str | None = None
    reviewer: str | None = None
    reason: str
    section_target: str | None = None


class ClaimReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_claims: int
    total_reviews: int
    approved: int = 0
    rejected: int = 0
    unreviewed: int = 0
    stale: int = 0
    invalid_routes: int = 0
    invalid_reviews: int = 0
    orphaned: int = 0
    entries: list[ClaimReviewOutcome] = Field(default_factory=list)
    orphan_review_ids: list[str] = Field(default_factory=list)
    limitation: str = (
        "Explicit named-reviewer decisions, not authenticated identity or scientific validation. "
        "Source and downstream grounding checks still apply."
    )


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def claim_review_fingerprints(claim: ScientificClaim) -> ClaimFingerprints:
    """Fingerprint raw extraction and exact linked evidence, ignoring evidence order."""
    return ClaimFingerprints(
        claim_sha256=_digest(claim.model_dump(mode="json", exclude={"evidence_items"})),
        evidence_sha256=_digest(
            sorted(
                (item.model_dump(mode="json") for item in claim.evidence_items),
                key=_canonical,
            )
        ),
    )


def _plain_path(path: Path) -> None:
    for current in (path, *path.parents):
        if current.exists() and is_link(current):
            raise ClaimReviewLedgerError("Linked claim-review ledger paths are not allowed")
    if path.exists() and not path.is_file():
        raise ClaimReviewLedgerError("The claim-review ledger must be a regular file")


def _read_bytes(path: Path) -> bytes:
    _plain_path(path)
    if not path.exists():
        return b""
    if path.stat().st_size > MAX_LEDGER_BYTES:
        raise ClaimReviewLedgerError("Claim-review ledger exceeds the supported size limit")
    data = path.read_bytes()
    if len(data) > MAX_LEDGER_BYTES:
        raise ClaimReviewLedgerError("Claim-review ledger exceeds the supported size limit")
    return data


def _decode(data: bytes) -> list[ClaimReview]:
    if not data:
        return []
    if not data.endswith(b"\n"):
        raise ClaimReviewLedgerError("Claim-review ledger has an incomplete final line")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClaimReviewLedgerError("Claim-review ledger must be UTF-8") from exc

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError("Non-finite JSON constant")

    records: list[ClaimReview] = []
    seen: set[str] = set()
    for number, line in enumerate(text.split("\n")[:-1], 1):
        try:
            payload = json.loads(
                line, object_pairs_hook=unique_pairs, parse_constant=reject_constant
            )
            record = ClaimReview.model_validate(payload)
            if record.review_id in seen:
                raise ValueError("Duplicate review ID")
        except (ValueError, TypeError) as exc:
            raise ClaimReviewLedgerError(f"Invalid claim-review record at line {number}") from exc
        seen.add(record.review_id)
        records.append(record)
    return records


def load_claim_reviews(path: Path) -> list[ClaimReview]:
    """Read complete atomic snapshots without creating files in immutable inputs."""
    return _decode(_read_bytes(Path(path)))


def _approval_has_evidence(claim: ScientificClaim) -> bool:
    return bool(claim.claim_text.strip() and claim.evidence_items) and all(
        item.evidence_id.strip() and item.text.strip() and item.content_hash.strip()
        for item in claim.evidence_items
    )


def append_claim_review(
    path: Path,
    claim: ScientificClaim,
    *,
    decision: ReviewDecision,
    support_strength: SupportStrength,
    reviewer: str,
    reason: str,
    section_target: str | None = None,
) -> ClaimReview:
    """Append one explicit decision, retaining the byte prefix under an OS lock.

    Atomic replacement publishes the unchanged prior ledger plus one line. A
    reader sees a complete old/new version, including during concurrent writes.
    """
    path = Path(path)
    _plain_path(path)
    if decision == "approve" and not _approval_has_evidence(claim):
        raise ClaimReviewLedgerError("Approval requires nonempty linked claim evidence")
    fingerprints = claim_review_fingerprints(claim)
    record = ClaimReview(
        **fingerprints.model_dump(),
        review_id=uuid.uuid4().hex,
        reviewed_at=datetime.now(UTC),
        claim_id=claim.claim_id,
        reviewer=reviewer,
        reason=reason,
        decision=decision,
        support_strength=support_strength,
        section_target=section_target,
    )
    line = (_canonical(record.model_dump(mode="json")) + "\n").encode("utf-8")
    with exclusive_lock(path.with_name(path.name + ".lock"), wait_seconds=10):
        before = _read_bytes(path)
        _decode(before)
        if len(before) + len(line) > MAX_LEDGER_BYTES:
            raise ClaimReviewLedgerError("Claim-review ledger exceeds the supported size limit")
        write_text(path, (before + line).decode("utf-8"))
    return record


def apply_claim_reviews(
    claims: Sequence[ScientificClaim],
    reviews: Sequence[ClaimReview],
    *,
    allowed_sections: Collection[str] | None = None,
) -> tuple[list[ScientificClaim], ClaimReviewReport]:
    """Apply only the latest matching decision to copies of raw extracted claims."""
    if len({claim.claim_id for claim in claims}) != len(claims):
        raise ClaimReviewLedgerError("Raw claims contain duplicate claim IDs")
    latest: dict[str, ClaimReview] = {}
    seen: set[str] = set()
    for supplied in reviews:
        try:
            record = ClaimReview.model_validate(supplied.model_dump(mode="json"))
        except (AttributeError, ValidationError) as exc:
            raise ClaimReviewLedgerError("Invalid in-memory claim-review record") from exc
        if record.review_id in seen:
            raise ClaimReviewLedgerError("Duplicate review IDs in claim-review history")
        seen.add(record.review_id)
        latest[record.claim_id] = record
    report = ClaimReviewReport(total_claims=len(claims), total_reviews=len(reviews))
    output: list[ScientificClaim] = []
    for original in claims:
        claim = original.model_copy(deep=True)
        review = latest.pop(claim.claim_id, None)
        status: Literal[
            "unreviewed", "approved", "rejected", "stale", "invalid_route", "invalid_review"
        ] = "unreviewed"
        reason = "No explicit human review recorded"
        if review is not None:
            current = claim_review_fingerprints(original)
            if (
                current.claim_sha256 != review.claim_sha256
                or current.evidence_sha256 != review.evidence_sha256
            ):
                status, reason = (
                    "stale",
                    "Latest review does not match the raw claim and linked evidence",
                )
                report.stale += 1
            elif (
                review.section_target is not None
                and allowed_sections is not None
                and review.section_target not in allowed_sections
            ):
                status, reason = (
                    "invalid_route",
                    "Reviewed section target is absent from allowed project sections",
                )
                report.invalid_routes += 1
            elif review.decision == "approve" and not _approval_has_evidence(claim):
                status, reason = "invalid_review", "Approval has no usable linked claim evidence"
                report.invalid_reviews += 1
            else:
                status = "approved" if review.decision == "approve" else "rejected"
                reason = review.reason
                claim.support_strength = review.support_strength
                claim.needs_human_review = False
                claim.allowed_language = list(allowed_language_for_support(review.support_strength))
                claim.forbidden_language = list(
                    forbidden_language_for_support(review.support_strength)
                )
                if review.section_target is not None:
                    claim.section_target = review.section_target
                if status == "approved":
                    report.approved += 1
                else:
                    report.rejected += 1
        if status not in {"approved", "rejected"}:
            report.unreviewed += 1
        output.append(claim)
        report.entries.append(
            ClaimReviewOutcome(
                claim_id=claim.claim_id,
                status=status,
                review_id=review.review_id if review else None,
                reviewer=review.reviewer if review else None,
                reason=reason,
                section_target=review.section_target if review else None,
            )
        )
    report.orphaned = len(latest)
    report.orphan_review_ids = [review.review_id for review in latest.values()]
    return output, report
