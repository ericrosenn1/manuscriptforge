from __future__ import annotations

from manuscriptforge.models.audit import AuditFinding
from manuscriptforge.models.claim import ScientificClaim
from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.utils.ids import stable_id


def audit_claims(manuscript: Manuscript, claims: list[ScientificClaim]) -> list[AuditFinding]:
    claim_ids = {claim.claim_id for claim in claims}
    findings: list[AuditFinding] = []
    for claim in claims:
        if not claim.evidence_items:
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", claim.claim_id + "no_evidence"),
                    severity="serious",
                    category="claim_support",
                    message="Claim has no evidence items.",
                    location=claim.claim_id,
                    suggested_fix="Attach evidence or remove the claim.",
                    related_claim_ids=[claim.claim_id],
                )
            )
        if claim.support_strength in {"unsupported", "needs_review"}:
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", claim.claim_id + "review"),
                    severity="warning",
                    category="claim_support",
                    message=f"Claim support strength is {claim.support_strength}.",
                    location=claim.claim_id,
                    suggested_fix="Add evidence, soften wording, or mark explicitly as a limitation.",
                    related_claim_ids=[claim.claim_id],
                )
            )
    for section in manuscript.sections:
        for claim_id in section.claim_ids:
            if claim_id not in claim_ids:
                findings.append(
                    AuditFinding(
                        finding_id=stable_id("aud", section.section_name + claim_id),
                        severity="serious",
                        category="claim_traceability",
                        message=f"Section references unknown claim ID {claim_id}.",
                        location=section.section_name,
                        suggested_fix="Regenerate or repair the claim registry.",
                        related_claim_ids=[claim_id],
                    )
                )
    return findings
