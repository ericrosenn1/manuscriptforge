# Architecture

ManuscriptForge keeps source documents, extracted representations, review decisions, and reports as separate artifacts. The offline corpus workflow uses ordinary files and Pydantic models; it does not require a database, service, or language model.

| Module | Responsibility | Main artifacts |
| --- | --- | --- |
| [config.py](../manuscriptforge/config.py), [models/project.py](../manuscriptforge/models/project.py) | Merge and validate project settings; check project inputs | project.yaml, validation findings |
| [style/document_extractors.py](../manuscriptforge/style/document_extractors.py) | Discover configured local samples, extract supported formats, cache text and source hashes | Extraction manifest, cached text, extraction report |
| [ingest/style_ingest.py](../manuscriptforge/ingest/style_ingest.py) | Recognize headings and split section text into stable chunks | StyleChunk IDs, text, section labels, hashes |
| [style/curation.py](../manuscriptforge/style/curation.py) | Persist chunk review state, apply explicit approvals/exclusions, report coverage, build cards | CSV/JSONL registry, decision log, Markdown/XLSX reports |
| [style/profiler.py](../manuscriptforge/style/profiler.py), [style/features.py](../manuscriptforge/style/features.py) | Calculate descriptive features on selected chunks | Global and section profiles, chunk index, style guide |
| [demo.py](../manuscriptforge/demo.py) | Supply original synthetic inputs and check the public workflow's outputs | Demo report, portable summary, integrity manifest |

## Research execution

The `research` command family adds a trusted, resumable manuscript DAG. It does not accept executable steps from project documents. The orchestration layer persists run state and verifies input, parameter, implementation, and dependency fingerprints before reusing a completed stage.

| Package | Role |
| --- | --- |
| `orchestration` | Static node contracts, isolated workers, durable scheduling, immutable snapshots, verified reuse, timeouts, cancellation, and final manifests |
| `research` | Project-specific DAG, provider settings, trusted stage handlers, CLI, and verified bundle assembly |
| `llm` | Typed provider boundary, explicit model requests, conservative budget accounting, and structured grounding checks |
| `retrieval` | Immutable lexical records, BM25/Jaccard scoring, filters, optional diversity selection, and index validation |
| `learning` | Explicit labeled preference pairs, grouped splits, train-only scaling, logistic fitting, and JSON model artifacts |
| `evaluation` | Versioned source/fact/case contracts, withheld labels, grouped leakage checks, separate functional metrics, and immutable evaluation reports |

Node workspaces are separate from source snapshots. The final bundle contains the selected source material, stage artifacts, manuscript formats, audit findings, and checksums. Computational success publishes an immutable latest pointer; it does not imply scientific approval. See [Research workflow](research-workflow.md).

Provider outputs remain candidates. Identifier and numeric checks establish specified forms of consistency, not unrestricted semantic entailment. The [evaluation protocol](evaluation.md) and [learning model notes](learning.md) describe the measured scope.

## Browser studio

The browser application under `web/` is a separate React and TypeScript client built with Vite. It adds an interactive workflow while preserving the Python toolkit and its file-based APIs. The client does not call a Python service or invoke a model provider.

| Layer | Responsibility |
| --- | --- |
| React interface | Workspace navigation, source inspection, review decisions, visual summaries, manuscript editing, and exports |
| `src/lib/types.ts` | Versioned workspace, source, passage, and analysis contracts |
| `src/lib/analysis.ts` | Section splitting, content hashes, explicit selection, and deterministic descriptive metrics |
| `src/lib/files.ts` | Bounded local document imports, validated workspace imports, DOCX output, and portable archive creation |
| `src/lib/storage.ts` | Local browser persistence and restoration |

Source records keep their extracted text and identifiers. Passage decisions update the workspace, and the profile is derived from approved passages. The manuscript is separately editable and is not treated as validated scientific output. The ZIP export bundles readable artifacts and a checksum manifest; JSON import validates the workspace contract before use.

The browser demo reuses the original synthetic prose from the Python demo. It does not execute the Python pipeline. Extraction, tokenization, and metrics may differ between engines; source-text reuse is not a numerical-parity claim. See the [studio guide](studio.md) for storage, format, and interpretation limits.

## Identity and curation

A source record includes its file hash and extraction status. A chunk records its source-relative filename, content hash, inferred section, and stable ID. Chunk IDs include source location, section, ordinal position, and text hash. Identical text in two source files therefore has the same content hash but distinct chunk IDs.

The registry keeps approval state and intended uses alongside each chunk. Exact duplicate hashes create a warning flag; they do not choose which copy to retain. Exclusion is an explicit decision. Rebuilding the registry preserves decisions, and the demo checks this behavior for its unchanged source files.

Profile construction rebuilds chunks from the extracted samples, then applies the curation policy. General project defaults allow unreviewed material; strict approval must be requested in configuration. The demo sets strict approval and creates the registry before profiling. Profiles and cards retain short source examples for traceability, so they can contain sensitive prose when run on a user's writing.

## Format and analysis limits

Markdown and text extraction read local text. DOCX extraction reads paragraphs and table cells but does not preserve complete document layout, comments, tracked-change semantics, or all embedded objects. PDF extraction requires a usable text layer and does not perform OCR or reconstruct complex reading order.

Heading recognition, sentence splitting, hedging terms, passive-voice estimates, noun-phrase patterns, and citation patterns use rules. They describe a chosen corpus and may misclassify unfamiliar structures. The software does not infer that a style sample provides evidence for a scientific claim.

## Experimental manuscript path

The original `draft` workflow below is retained separately from the newer `research` DAG and structured model boundary.

Other retained modules provide project/table/source ingestion, claim extraction and citation mapping, section drafting, audits, reviewer-style checks, feedback capture, and exports. `pipeline/workflow.py` coordinates these actions when explicitly invoked. `llm/mock_provider.py` supplies deterministic test behavior, while `llm/openai_provider.py` is an optional network-capable adapter.

This path is separate from the offline demonstration. Audit findings flag patterns in supplied inputs for review. Mock-provider outputs and style benchmarks exercise deterministic rules; live-model writing quality has not been validated by the public demo.
