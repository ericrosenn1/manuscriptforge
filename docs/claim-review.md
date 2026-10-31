# Author review of claims

Extracted claims and author decisions are separate records. A model cannot approve
its own claims. Inspect a completed claim stage, including one retained from a
failed run:

```bash
manuscriptforge research claims PROJECT RUN_ID
```

Record a decision against an exact source-bound claim:

```bash
manuscriptforge research review-claim PROJECT RUN_ID CLAIM_ID \
  --decision approve --support direct --reviewer "Author name" \
  --reason "Checked the statement against the cited source and table row."
manuscriptforge research run PROJECT
```

Use `--decision reject --support unsupported` to exclude a claim. An optional
`--section SECTION` records an explicit routing decision to a section enabled in
`project.yaml`. Support labels express the reviewer's judgment; they do not prove
scientific validity. An approval must have evidence and an explicit reviewer,
reason, and support classification.

Decisions are appended to `claim_reviews.jsonl` using a process lock and atomic
publication. They include separate hashes of the original claim and its evidence.
The latest decision governs; a stale or invalid latest decision does not revive an
older approval. A source or claim change requires renewed review. The ledger is a
local provenance record, not a cryptographic identity attestation.

The claim stage retains `claim_registry_unreviewed.json`, writes the reviewed copy
to `claim_registry.json`, and records applied, stale, invalid, and orphaned
decisions in `claim_review_audit.json`. Source claims and earlier decisions remain
available. Changing only the review ledger invalidates the claim stage and its
descendants; ingestion and the style profile can reuse verified cached results.

The original synthetic demo includes clearly labeled, scripted fixture reviews so
the complete workflow can be exercised. These are not human scientific reviews.
Ordinary projects receive no automatic approvals. All generated manuscripts still
require author review, including when their source claims have been approved.

## Failure and citation records

Section attempts retain the exact request and provider usage. A failed model
attempt also retains bounded response capture and validation diagnostics in its
attempt directory; failed output never becomes a successful section artifact.
These files may contain source and candidate prose and should be reviewed before
sharing.

The manuscript bibliography contains cited records. The full input citation
registry remains in the ingestion artifacts, including unused entries, so an
uncited note or placeholder is not silently presented as a manuscript reference.
