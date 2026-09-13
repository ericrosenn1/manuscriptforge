# Working with your own project

A corpus is a collection of writing samples. The pipeline splits the extracted text into **chunks**, or section-labeled passages. **Curation** means deciding which passages to include or exclude; the **registry** is the table that stores passage records and review decisions.

A **style profile** contains descriptive statistics and examples from selected passages. A **style card** is a compact report for one manuscript section or the overall collection. Reports keep passage IDs and source links so you can trace each summary back to its inputs.

Inspect available commands with `manuscriptforge --help` and a command's options with `manuscriptforge COMMAND --help`. Use a separate project directory for your data and derivatives.

Activate the virtual environment shown in the README before using the commands below. On Windows, use `.\.venv\Scripts\Activate.ps1`, or replace `manuscriptforge` with `.\.venv\Scripts\manuscriptforge.exe`.

```text
manuscriptforge init ../writing-project
```

This creates a project configuration and starter inputs. Replace the starter content in `inputs/abstract.md` and `inputs/methods.md`, and set the project name, field, audience, and manuscript settings in `project.yaml`. Add only writing you intend to analyze under `style_corpus/academic_manuscript/`. The corpus extractor supports `.md`, `.txt`, `.docx`, and text-extractable `.pdf` files.

## Configure curation

Edit these fields in the existing `project.yaml` for a local corpus workflow that requires explicit approval:

```yaml
privacy:
  local_only: true
llm:
  provider: mock
sources:
  allow_network_enrichment: false
style:
  active_mode: academic_manuscript
  include_modes: [academic_manuscript]
  exclude_modes: [coursework_explanatory]
  curation:
    use_chunk_registry: true
    require_chunk_approval: true
    include_needs_review_chunks: false
```

The defaults do not require approval. Set strict approval deliberately and build the registry before profiling. The style modes select corpus categories; they do not certify authorship, privacy, or suitability.

## Extract, inspect, and approve

```text
manuscriptforge validate ../writing-project
manuscriptforge extract-style-corpus ../writing-project --mode academic_manuscript
manuscriptforge build-style-chunk-registry ../writing-project --mode academic_manuscript
manuscriptforge style-chunk-report ../writing-project
```

The general validator also checks drafting inputs and may report missing tables or citations that are not needed for a corpus-only profile. Inspect its findings rather than assuming every warning applies to your intended use.

Read the registry and review files under `planning/intake/style_chunks/`. To approve a reviewed source named `sample.md`, or exclude an older revision named `older_revision.md`, use their actual project-relative filenames:

```text
manuscriptforge style-chunk-approve ../writing-project --source-file style_corpus/academic_manuscript/sample.md --approve --approve-for style_profile --note "Reviewed source for descriptive profiling."
manuscriptforge style-chunk-approve ../writing-project --source-file style_corpus/academic_manuscript/older_revision.md --exclude --note "Older revision excluded after review."
```

Use `--chunk-id` to review individual passages. `--source-file` applies to all matching chunks from that source, so inspect the matches before approving. Approval notes are stored in the decision log. Exact duplicate warnings require a deliberate inclusion/exclusion decision.

```text
manuscriptforge profile-style ../writing-project
manuscriptforge style-coverage-report ../writing-project --mode academic_manuscript
manuscriptforge build-style-cards ../writing-project --mode academic_manuscript
```

The profile command writes a run under `outputs/runs/` and updates `outputs/latest/`. Coverage and cards are written under `planning/intake/`. Profile examples and extraction records preserve text and source information. Review them before sharing or committing anything.

An **intended use** names a task the passage is approved for. On a new chunk, `--approve-for` grants only the named use. On an already approved chunk, it adds the named use without removing existing ones. For example, `style_profile` permits descriptive profiling but not evaluation-set export; use `style_eval` for `build-style-eval`. Omitting `--approve-for` grants all supported uses only when the chunk has no recorded uses, and otherwise preserves its existing uses.

## Optional integrations

Core extraction and profiling need no model provider. The optional `openai` extra installs the dependency for the explicit OpenAI adapter, and the `ui` extra enables the local Streamlit interface. Network-capable settings and credentials are configured separately. Keep credentials and private project outputs outside tracked source.

For an end-to-end example with known content and a checked rerun, use the [synthetic demo](demo.md).
