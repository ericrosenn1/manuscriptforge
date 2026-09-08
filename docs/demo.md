# Offline demo walkthrough

Run `manuscriptforge demo OUTPUT_DIRECTORY` after installation. The Python equivalent is:

```python
from manuscriptforge.demo import run_demo

summary = run_demo("../manuscriptforge-demo")
print(summary["approved_chunks"])
```

The demo writes only within the directory you choose. It accepts a new or empty directory. For an intact completed demo, it verifies file hashes and content relationships and returns the same summary without rewriting any file. A changed, incomplete, or unrelated nonempty directory is rejected. Use a new destination to create another run. Symbolic links and Windows junctions in the destination path or existing demo tree are rejected.

## The collection

All passages were written as original synthetic examples for this project. They concern an imagined distance-sensor documentation procedure. There are no actual measurements, real-paper citations, or personal writing samples. The text is supplied in the source code; no model provider is called at runtime.

| Source | Format | Sections | Curation decision |
| --- | --- | --- | --- |
| `protocol.md` | Markdown | Abstract, Introduction, Methods; a References sentinel | Approve three passages for the profile |
| `run_note.txt` | Plain text | Results, Discussion, Limitations | Approve three passages for the profile |
| `methods_duplicate.docx` | DOCX | Exact copy of Methods | Explicitly exclude the duplicate |

The generated DOCX has synthetic metadata and fixed archive timestamps. It is built from the same source string as the Markdown methods section. Text and DOCX source content are deterministic. Report timestamps and absolute paths in extraction records vary between newly created projects; full output directories are not claimed to be byte-identical. The compact `demo_summary.json` is portable and deterministic.

## What runs

The wrapper calls the real public APIs in sequence:

1. Write and validate the project configuration, setting local-only operation, no network enrichment, and strict chunk approval.
2. Extract the three source files, recording source hashes, extraction status, and cached text.
3. Split them into seven section chunks, excluding the References sentinel.
4. Build the chunk registry. Both methods copies receive a `repeated_boilerplate` warning because their content hashes match.
5. Record scripted approval decisions for six chunks and an exclusion for the DOCX duplicate. Rebuild the registry and check that IDs, statuses, allowed uses, and notes persist.
6. Build the global and section profiles, coverage report, and eight style cards using the curated corpus.
7. Check saved content against the expected relationships and write a manifest of output file sizes and SHA-256 hashes.

No drafting provider is invoked. The general project validator reports missing recommended drafting notes, result tables, and citations. Those warnings are recorded explicitly because these inputs are intentionally absent from a corpus demonstration. There must be no project validation errors.

## Reading the outputs

Start with `DEMO_REPORT.md` and `outputs/profile/style_guide.md`. The JSON profile contains six approved chunks, 432 words, 26 sentences, and the original source text for each represented section. `planning/intake/style_chunks/style_chunk_review.md` shows six approvals and one exclusion, while retaining both duplicate warning flags.

`planning/intake/style_coverage_report.md` reports one approved chunk for each of Abstract, Introduction, Methods, Results, Discussion, and Limitations. The eight cards include those six sections, an empty Figure Legends card, and an overall card. Empty section profiles/cards make missing coverage visible.

Coverage labels are count bins: absent is 0 approved chunks, sparse is 1 to 2, usable is 3 to 7, and strong is 8 or more. They are not writing-quality ratings, scientific validity checks, or sufficient sample-size recommendations.

The [expected excerpt](../examples/demo_expected_summary.json) is a subset of the real summary. Full generated projects are deliberately not committed. The manifest helps detect accidental edits; it is a local integrity record, not a signed attestation or a replacement for content review.
