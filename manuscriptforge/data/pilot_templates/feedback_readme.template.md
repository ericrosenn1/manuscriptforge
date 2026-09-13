# Feedback Folder Instructions

<!--
Use this folder for optional JSONL feedback files used by capture-feedback --from-file.
Capture feedback after a draft and review plan exist.
-->

## JSONL Expectations

Each line should be a JSON object that references an existing review item or variant. Do not add private comments that should not become part of local style memory.

## Suggested Workflow

1. Run draft.
2. Run style-benchmark.
3. Run review-plan.
4. Run generate-variants.
5. Capture accepted/rejected feedback.
