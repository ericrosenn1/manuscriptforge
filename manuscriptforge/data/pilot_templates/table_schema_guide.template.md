# Table Schema Guide

<!--
Use this guide to map table columns to ManuscriptForge roles.
Run infer-table-schema after adding CSV, TSV, XLSX, or XLS files.
-->

## Common Roles

| Role | Typical columns | Notes |
| --- | --- | --- |
| feature | gene, marker, pathway, protein, feature | Identifies the result row. |
| comparison | comparison, contrast | Names the group or condition contrast. |
| effect_size | log2_fold_change, log2fc, estimate | Numeric effect estimate. |
| p_value | p, p_value | Raw p-value. |
| adjusted_p_value | padj, q_value, fdr, adjusted_p_value | Multiple-testing adjusted value. |
| confidence_interval | ci, lower_ci, upper_ci | Interval estimates. |
| sample_size | n, sample_size | Number of samples, cases, or observations. |
| figure_reference | figure, figure_ref | Links rows to figures. |
| notes | notes, note | Human-readable cautions. |

## Review Before Drafting

- Confirm that detected p-value columns are numeric.
- Confirm that effect-size direction is documented.
- Add thresholds only when they match the analysis plan.
- Avoid using raw private data tables when summarized results are enough.
