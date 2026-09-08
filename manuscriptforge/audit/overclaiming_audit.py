from __future__ import annotations

import re

from manuscriptforge.models.audit import AuditFinding
from manuscriptforge.models.claim import ScientificClaim
from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.text import split_sentences

STRONG_LANGUAGE_PATTERNS = [
    (r"\bproves?\b|\bproved\b", "proof language"),
    (r"\bdemonstrates?\s+causality\b", "causal demonstration language"),
    (r"\bestablish(?:es|ed)?\b", "establishing language"),
    (r"\bconfirms?\b|\bconfirmed\b", "confirmation language"),
    (r"\bdetermines?\b|\bdetermined\b", "deterministic language"),
    (r"\bvalidates?\s+clinically\b|\bclinically\s+validated\b", "clinical validation language"),
    (r"\bcures?\b|\bcured\b|\bguarantees?\b", "clinical guarantee language"),
    (r"\bcauses?\b|\bcaused\b|\bcausal\b", "causal language"),
    (r"\bdrives?\b|\bdrove\b|\bled\s+to\b|\bleads\s+to\b|\bresulted\s+in\b", "mechanistic causal language"),
    (r"\bpredicts?\b|\bpredicted\b|\bdiagnoses?\b|\bdiagnostic\b", "predictive or diagnostic language"),
]


def audit_overclaiming(manuscript: Manuscript, claims: list[ScientificClaim]) -> list[AuditFinding]:
    claim_by_id = {claim.claim_id: claim for claim in claims}
    findings: list[AuditFinding] = []
    for section in manuscript.sections:
        risky_claims = [
            claim_by_id[claim_id]
            for claim_id in section.claim_ids
            if claim_id in claim_by_id
            and claim_by_id[claim_id].support_strength in {"weak", "unsupported", "needs_review", "indirect"}
        ]
        section_sentences = split_sentences(section.content) or [section.content]
        for pattern, label in STRONG_LANGUAGE_PATTERNS:
            for sentence in section_sentences:
                if not re.search(pattern, sentence, re.I):
                    continue
                findings.append(
                    AuditFinding(
                        finding_id=stable_id("aud", section.section_name + label + sentence),
                        severity="serious" if risky_claims else "warning",
                        category="overclaiming",
                        message=f"Potentially excessive {label} detected: {sentence}",
                        location=section.section_name,
                        suggested_fix="Replace with cautious phrasing such as 'suggests', 'is consistent with', or 'may indicate' unless direct causal evidence exists.",
                        related_claim_ids=[claim.claim_id for claim in risky_claims],
                    )
                )
    for claim in claims:
        if claim.support_strength not in {"weak", "unsupported", "needs_review", "indirect"}:
            continue
        for forbidden in claim.forbidden_language:
            if forbidden.lower() in claim.claim_text.lower():
                findings.append(
                    AuditFinding(
                        finding_id=stable_id("aud", claim.claim_id + forbidden),
                        severity="serious",
                        category="overclaiming",
                        message=f"Weak or review-needed claim uses forbidden language: '{forbidden}'.",
                        location=claim.claim_id,
                        suggested_fix="Revise the claim text before drafting or mark it unsupported.",
                        related_claim_ids=[claim.claim_id],
                    )
                )
    return findings
