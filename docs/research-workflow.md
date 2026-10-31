# Research workflow

The research engine executes a fixed, trusted dependency graph over an explicit project directory. Original files remain inputs; intermediate artifacts, model candidates, audit findings, and export bundles are recorded separately. Source text cannot register executable handlers.

## A local demonstration

```bash
manuscriptforge research demo ../manuscriptforge-demo --no-run
manuscriptforge research plan ../manuscriptforge-demo
manuscriptforge research run ../manuscriptforge-demo --provider mock
```

The research demo contains original synthetic nonmedical inputs. The mock provider exercises the execution and validation contracts without a model request. A completed mock run is a software demonstration, not evidence of generative writing quality. Review all resulting findings.

The older `manuscriptforge demo NEW_DIR` command demonstrates document extraction and passage curation; it is a different, smaller example.

## Your own project

Use a separate project directory and the [usage guide](usage.md) to prepare `project.yaml`, writing samples, methods notes, interpretation notes, result tables, and supplied sources. Review table column roles and analysis thresholds explicitly. The software cannot decide whether your scientific design or interpretation is valid.

`research plan PROJECT` shows the stages and selected inputs before execution. `research run PROJECT --provider mock` exercises the local path. The trusted plan contains validation, ingestion, profiling, claim construction, section tasks, assembly, audit, and export. Profiling and claim construction can proceed independently; section tasks wait for their declared dependencies.

The execution layer snapshots declared input files, records their hashes, and creates isolated workspaces for stages. Its resume and cache checks include inputs, parameters, software identity, and dependency artifacts. Changing an upstream source invalidates dependent work. Successful unaffected stages can be reused after verification.

## State, resumption, and cancellation

Use the run ID printed by the command:

```bash
manuscriptforge research status PROJECT RUN_ID
manuscriptforge research verify PROJECT RUN_ID
manuscriptforge research resume PROJECT RUN_ID
manuscriptforge research cancel PROJECT RUN_ID
```

Status reads durable state. Verification rehashes artifacts. Resume uses the recorded operational settings and preserves verified successful work. Cancellation targets the run's owned workers; successful artifacts remain available. Timeouts and bounded retries are operational controls, not permission to change scientific assumptions.

The engine publishes `PROJECT/outputs/latest.json` only for a verified successful run. This file points to an immutable finalized run manifest. A failed or running attempt does not replace a successful latest pointer. The CLI prints the exact run location; inspect it instead of guessing a directory name.

## Candidates and audit findings

The model-facing schema associates paragraphs with supplied claims, citations, and style examples. It checks reference membership, claim-specific numeric literals, structured fact fields where supplied, citation identifiers, copied style-only passages, and word limits. Source instructions are treated as untrusted data.

These are structural and syntactic constraints. A model can still produce semantically wrong prose using known words and numbers. Citation relevance is not citation entailment. Style samples are not factual evidence. A human author must review the candidate and its sources.

The final export node writes `research_bundle.zip` with manuscript Markdown, DOCX, LaTeX, JSON, stage artifacts, source snapshots, settings, and a file manifest. ZIP payloads are checked against recorded sizes and SHA-256 digests. Inspect `stages/audit/qualification.json`, audit reports, and source records before interpreting the result. Computational completion and scientific acceptance are separate statuses.

## Optional model access and cost control

The default is local mock execution. External generation requires all of the following: an explicitly selected provider, network opt-in, a project configuration that allows external processing, a configured key, and a budget ledger with a positive cap.

Install the optional SDK first with `python -m pip install -e ".[openai]"` from the repository checkout. Keep using the same virtual environment as the CLI.

For an intentionally configured project:

```bash
manuscriptforge research run PROJECT --provider openai --model gpt-6-luna \
  --allow-network --budget-usd 10 --ledger LOCAL_LEDGER.json
manuscriptforge research budget LOCAL_LEDGER.json --limit-usd 10
```

On PowerShell, place the command on one line instead of using Bash's backslash continuation. Set `privacy.local_only: false` in the selected project only after deciding that its content may be sent to the provider. Keep the API key in the local environment or ignored `.env`, never in command arguments or shared files.

Budget reservations are conservative and shared across calls. Provider usage, model identity, latency, and failures are recorded when available. Missing usage is not reported as zero. A budget limit is an execution bound; it does not guarantee a particular answer quality.

## Retrieval, preference learning, and evaluation

The retrieval module exposes transparent lexical rankings with filters and optional diversity selection. The [learned ranker](learning.md) fits explicit pairwise style preferences and leaves source texts unchanged. Neither capability is a scientific evidence adjudicator.

```bash
manuscriptforge research evaluate LOCAL_EVALUATION_OUTPUT --candidate mock
```

This writes a paired, grouped held-out functional evaluation. The default synthetic tasks deliberately include missing and conflicting evidence; negative results are retained. A command finishing is not a passing evaluation. Read `summary.json`, `REPORT.md`, and the per-case JSONL files. See the [evaluation protocol](evaluation.md) for denominators, nulls, uncertainty, and the difference between synthetic mechanism checks and model-quality evidence.

## Source-only model comparison

For a separately authorized paid comparison, the CLI exposes the source-only protocol:

```bash
manuscriptforge research compare-models local/model-comparison local/model-ledger.json 10 --allow-network --baseline gpt-6-luna --candidate gpt-6.1-sol --split dev --credentials-from PROJECT
```

The three positional arguments are the output root, shared ledger, and budget cap. The command supplies raw sources and queries, not reference fact values. Use only approved data. Model selection belongs on development data; final test data must remain untouched until the method is frozen. This command and `research evaluate` measure different input contracts and their results should not be merged into one quality score.

For the separately versioned quantity dataset, add `--dataset manuscriptforge/data/benchmarks/quantities-v1.json --scoring-version 2.1-quantities`. The model adapters default to quantity guard 2.1; `--baseline-quantity-protocol` and `--candidate-quantity-protocol` select explicit historical comparisons. Guard and scoring versions are reported separately. Add `--capture-policy failures` to retain local raw responses for diagnosis, or `all` for every response; the default is `off`. The [dataset card](quantity-benchmark.md) defines literal ground truth and limitations.

## Search and preference ranking

After a successful run, inspect its verified evidence index:

```bash
manuscriptforge research search PROJECT RUN_ID "collection records" --limit 5
```

Use `research train-style OUTPUT --dataset PAIRS.json` to train from explicit labels, or `--synthetic` for the disclosed mechanism demonstration. `research rank-style MODEL.json PASSAGES.json --output NEW_RANKING.json` ranks a JSON list of passages without changing their text. See the [preference guide](learning.md) for grouping and evaluation scope.
