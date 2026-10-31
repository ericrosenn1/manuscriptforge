# Lexical retrieval

`manuscriptforge.retrieval` provides an offline inverted index with two explicitly named ranking strategies. It does not use embeddings, train a model, or establish whether a source supports a scientific claim. The existing `EvidenceIndex` keeps its original Jaccard behavior, with deterministic handling of tied scores.

```python
from pathlib import Path
from manuscriptforge.retrieval import LexicalIndex, RetrievalFilter, RetrievalRecord

records = [
    RetrievalRecord.from_text(
        id="passage-001",
        text="The versioned archive preserves original records and processing notes.",
        source_id="document-001",
        metadata={"section": "methods", "style_mode": "academic_manuscript"},
    ),
]
# Opt into BM25. The constructor's compatibility default is Jaccard.
index = LexicalIndex(records, strategy="bm25")
hits = index.search(
    "versioned archive",
    limit=5,
    filters=RetrievalFilter(sections={"methods"}),
)
for hit in hits:
    print(hit.rank, hit.record_id, hit.score, hit.content_hash)

index.save(Path("retrieval-index.json"))  # Existing files are preserved by default.
restored = LexicalIndex.load(Path("retrieval-index.json"))
assert restored.index_sha256 == index.index_sha256
```

## Ranking rules

BM25 uses `k1=1.5`, `b=0.75`, unique query terms, and positive inverse document frequency:

```text
IDF(term) = log(1 + (N - df(term) + 0.5) / (df(term) + 0.5))
score = sum(IDF(term) * tf(term) * (k1 + 1)
            / (tf(term) + k1 * (1 - b + b * document_length / average_length)))
```

Jaccard scores are the size of the query/document token-set intersection divided by the size of their union. Unknown query terms contribute to its denominator. Empty or unmatched queries return no results. All tied scores sort by record ID, independently of input order.

No stop words are removed unless `stop_words` is explicitly supplied. The default Unicode tokenizer case-folds words and numbers, retains internal hyphens and apostrophes, and normalizes typographic apostrophes and minus signs. Decimal punctuation still creates separate lexical tokens; tokenization is not numeric reasoning. `tokenizer="legacy"` uses the existing English-oriented `word_tokens` function for exact comparison with the old retrieval path. The tokenizer version and stop-word policy are serialized.

## Filters and diversity

`RetrievalFilter` accepts source IDs, exact text hashes, sections, and style modes. These conditions are conjunctive. `None` leaves a dimension unrestricted; an empty set matches nothing. Section metadata can use `section` or the existing `section_type` key. Filters constrain eligibility without refitting corpus statistics.

`search(..., mmr_lambda=0.6)` enables maximal marginal relevance over a bounded candidate pool. It combines max-normalized relevance with token-set Jaccard redundancy, using `lambda * relevance - (1 - lambda) * redundancy`. Results retain the original relevance score and expose the separate selection score and redundancy penalty. `mmr_lambda=1` preserves relevance ordering. Diversity is lexical and does not guarantee different viewpoints, independent evidence, or author diversity.

## Integrity and provenance

Records are frozen and snapshot nested metadata. `content_hash` must be the SHA-256 of the exact UTF-8 record text. When an original binary or source-file hash is also available, retain it separately in metadata such as `source_hash`; do not substitute it for the record-text hash.

Serialized indexes contain records, configuration, document lengths, document frequencies, postings, a corpus fingerprint, and an index fingerprint. Loading verifies the checksum, each record's text hash, tokenizer/schema versions, and all derived statistics by rebuilding them. Altered statistics are rejected even when an outer checksum has been recomputed. These checks establish internal consistency, not authorship or authenticity against a malicious party able to replace every input and checksum.

The saved index contains source text. Review it before sharing. `save` refuses to replace an existing artifact unless `overwrite=True` is deliberately requested.

## Local performance measurement

```bash
python -m manuscriptforge.retrieval.benchmark --output local/retrieval-benchmarks --sizes 100 1000 10000 --queries 60
```

Each invocation writes a new directory containing `benchmark.json` and `REPORT.md`. It measures the existing full scan, indexed Jaccard, and BM25 under both the compatibility and Unicode tokenizers. Reports distinguish construction, queries, validated reloads, and optional MMR. They record the platform, Python version, seed, corpus fingerprints, and explicit limitations. The fixture text is synthetic and nonclinical. Timing results describe the measured machine and workload; they do not validate relevance or imply a universal speedup.

## Held-out source ranking

```python
from pathlib import Path
from manuscriptforge.evaluation.synthetic import build_synthetic_dataset
from manuscriptforge.retrieval.evaluation import run_ranking_comparison

run = run_ranking_comparison(
    build_synthetic_dataset(), Path("local/retrieval-evaluations"), split="test"
)
```

The adapter reads only the public query and source text. It ignores supplied fact tuples and cannot access expected source labels. Both strategies use the declared Unicode tokenizer, with no threshold fitted to the evaluation split. Ranking-only predictions contain no generated assertions. These reports measure ranking and no-result behavior, not factual generation. Lexical overlap alone cannot resolve conflicting evidence or reliably decide when a question should be declined. Null differences and failures on such cases are retained.
