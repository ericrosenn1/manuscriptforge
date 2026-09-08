# Architecture

ManuscriptForge keeps source documents, extracted representations, review decisions, and reports as separate artifacts. The offline corpus workflow uses ordinary files and Pydantic models; it does not require a database, service, or language model.

| Module | Responsibility | Main artifacts |
| --- | --- | --- |
| `config.py`, `models/project.py` | Merge and validate project settings; check project inputs | `project.yaml`, validation findings |
| `style/document_extractors.py` | Discover configured local samples, extract supported formats, cache text and source hashes | Extraction manifest, cached text, extraction report |
| `ingest/style_ingest.py` | Recognize headings and split section text into stable chunks | `StyleChunk` IDs, text, section labels, hashes |
| `style/curation.py` | Persist chunk review state, apply explicit approvals/exclusions, report coverage, build cards | CSV/JSONL registry, decision log, Markdown/XLSX reports |
| `style/profiler.py`, `style/features.py` | Calculate descriptive features on selected chunks | Global and section profiles, chunk index, style guide |
| `demo.py` | Supply original synthetic inputs and check the public workflow's outputs | Demo report, portable summary, integrity manifest |

## Identity and curation

A source record includes its file hash and extraction status. A chunk records its source-relative filename, content hash, inferred section, and stable ID. Chunk IDs include source location, section, ordinal position, and text hash. Identical text in two source files therefore has the same content hash but distinct chunk IDs.

The registry keeps approval state and intended uses alongside each chunk. Exact duplicate hashes create a warning flag; they do not choose which copy to retain. Exclusion is an explicit decision. Rebuilding the registry preserves decisions, and the demo checks this behavior for its unchanged source files.

Profile construction rebuilds chunks from the extracted samples, then applies the curation policy. General project defaults allow unreviewed material; strict approval must be requested in configuration. The demo sets strict approval and creates the registry before profiling. Profiles and cards retain short source examples for traceability, so they can contain sensitive prose when run on a user's writing.

## Format and analysis limits

Markdown and text extraction read local text. DOCX extraction reads paragraphs and table cells but does not preserve complete document layout, comments, tracked-change semantics, or all embedded objects. PDF extraction requires a usable text layer and does not perform OCR or reconstruct complex reading order.

Heading recognition, sentence splitting, hedging terms, passive-voice estimates, noun-phrase patterns, and citation patterns use rules. They describe a chosen corpus and may misclassify unfamiliar structures. The software does not infer that a style sample provides evidence for a scientific claim.

## Experimental manuscript path

Other retained modules provide project/table/source ingestion, claim extraction and citation mapping, section drafting, audits, reviewer-style checks, feedback capture, and exports. `pipeline/workflow.py` coordinates these actions when explicitly invoked. `llm/mock_provider.py` supplies deterministic test behavior, while `llm/openai_provider.py` is an optional network-capable adapter.

This path is separate from the offline demonstration. Audit findings depend on supplied inputs and heuristic rules; they do not establish the truth of a claim, adequacy of an experimental design, citation correctness, or preservation of scientific meaning. Mock outputs and style benchmarks are development instruments, not evidence of live writer performance. No unattended development controller or training service is part of the public package.
