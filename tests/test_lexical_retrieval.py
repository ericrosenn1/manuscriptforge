import json
import math
from dataclasses import FrozenInstanceError

import pytest

from manuscriptforge.evidence.evidence_index import EvidenceIndex
from manuscriptforge.models.claim import EvidenceItem
from manuscriptforge.retrieval import (
    LexicalIndex,
    RetrievalFilter,
    RetrievalRecord,
    text_sha256,
    tokenize,
)


def record(id, text, **metadata):
    return RetrievalRecord.from_text(id, text, f"source-{id}", metadata)


def checksum(payload):
    return text_sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def test_bm25_matches_hand_calculated_frequency_and_length_example():
    index = LexicalIndex(
        [record("a", "alpha alpha beta"), record("b", "alpha gamma"), record("c", "delta")],
        strategy="bm25",
    )
    assert index.document_lengths == (3, 2, 1)
    assert index.average_document_length == 2
    assert index.document_frequencies["alpha"] == 2
    hits = index.search("alpha")
    expected = math.log(1 + 1.5 / 2.5) * (2 * 2.5) / (2 + 1.5 * (0.25 + 0.75 * 3 / 2))
    assert [hit.record_id for hit in hits] == ["a", "b"]
    assert hits[0].score == pytest.approx(expected)
    assert hits[1].score == pytest.approx(math.log(1.6))
    assert index.search("alpha alpha")[0].score == hits[0].score


def test_jaccard_default_and_legacy_tokenizer_match_original_behavior():
    records = [record("a", "alpha beta"), record("b", "alpha beta gamma")]
    index = LexicalIndex(records, tokenizer="legacy")
    assert index.strategy == "jaccard"
    assert [hit.score for hit in index.search("alpha delta")] == pytest.approx([1 / 3, 1 / 4])
    assert tokenize("α2 10.5 first-person", mode="unicode") == ("α2", "10", "5", "first-person")
    assert tokenize("α2 10.5 first-person", mode="legacy") == ("first-person",)


def test_empty_queries_empty_corpus_and_stopwords_have_no_fake_hits():
    assert LexicalIndex([]).search("alpha") == []
    index = LexicalIndex([record("a", ""), record("b", "alpha")], stop_words=["alpha"])
    assert index.search("alpha") == []
    assert index.search("") == []
    assert index.search("absent") == []
    assert index.search("alpha", 0) == []
    assert LexicalIndex([record("a", "")], strategy="bm25").search("alpha") == []


def test_ties_are_stable_across_input_order_and_include_provenance():
    records = [record("b", "alpha"), record("a", "alpha")]
    first = LexicalIndex(records, strategy="bm25")
    second = LexicalIndex(reversed(records), strategy="bm25")
    assert first.to_dict() == second.to_dict()
    assert [hit.record_id for hit in first.search("alpha")] == ["a", "b"]
    hit = first.search("alpha")[0]
    assert hit.source_id == "source-a"
    assert hit.content_hash == text_sha256(hit.text)
    assert hit.rank == 1


def test_records_snapshot_nested_metadata_and_are_immutable():
    metadata = {"section": "methods", "tags": ["original"], "nested": {"value": 1}}
    item = RetrievalRecord.from_text("a", "alpha", "source", metadata)
    metadata["tags"].append("changed")
    metadata["nested"]["value"] = 2
    assert item.metadata["tags"] == ("original",)
    assert item.metadata["nested"]["value"] == 1
    with pytest.raises(TypeError):
        item.metadata["section"] = "results"
    with pytest.raises(FrozenInstanceError):
        item.text = "modified"
    with pytest.raises(ValueError, match="hash mismatch"):
        RetrievalRecord("a", "changed", "source", item.content_hash)


def test_source_hash_section_and_mode_filters_are_conjunctive():
    methods = record("a", "alpha", section_type="methods", style_mode="academic")
    results = record("b", "alpha", section="results", style_mode="coursework")
    index = LexicalIndex([methods, results], strategy="bm25")
    filters = RetrievalFilter(
        source_ids={"source-a"},
        content_hashes={methods.content_hash},
        sections={"methods"},
        style_modes={"academic"},
    )
    assert [hit.record_id for hit in index.search("alpha", filters=filters)] == ["a"]
    assert not index.search("alpha", filters=RetrievalFilter(source_ids=set()))
    assert not index.search("alpha", filters=RetrievalFilter(sections={"unknown"}))
    assert [
        hit.record_id
        for hit in index.search("alpha", filters=RetrievalFilter(sections={"results"}))
    ] == ["b"]


def test_filters_snapshot_iterables_and_ignore_nonstring_section_metadata():
    filters = RetrievalFilter(source_ids=(item for item in ["source-a"]))
    index = LexicalIndex([record("a", "alpha", section={"unrecognized": "value"})])
    assert index.search("alpha", filters=filters)
    assert not index.search("alpha", filters=RetrievalFilter(sections={"methods"}))


def test_mmr_diversifies_repeated_text_and_preserves_relevance_scores():
    index = LexicalIndex(
        [record("a", "alpha beta"), record("b", "alpha beta"), record("c", "alpha gamma")]
    )
    baseline = index.search("alpha", 2)
    diverse = index.search("alpha", 2, mmr_lambda=0.5)
    assert [hit.record_id for hit in baseline] == ["a", "b"]
    assert [hit.record_id for hit in diverse] == ["a", "c"]
    assert diverse[1].score == baseline[1].score
    assert diverse[1].diversity_penalty == pytest.approx(1 / 3)
    assert [hit.record_id for hit in index.search("alpha", 2, mmr_lambda=1)] == ["a", "b"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": -1},
        {"limit": True},
        {"mmr_lambda": float("nan")},
        {"mmr_lambda": 1.1},
        {"limit": 2, "candidate_pool_size": 1},
    ],
)
def test_invalid_search_parameters_fail_clearly(kwargs):
    with pytest.raises(ValueError):
        LexicalIndex([record("a", "alpha")]).search("alpha", **kwargs)


def test_serialized_index_roundtrips_and_refuses_accidental_overwrite(tmp_path):
    index = LexicalIndex(
        [record("b", "alpha beta", section="methods"), record("a", "beta gamma")],
        strategy="bm25",
        stop_words=["gamma"],
    )
    path = index.save(tmp_path / "index.json")
    loaded = LexicalIndex.load(path)
    assert loaded.index_sha256 == index.index_sha256
    assert [hit.to_dict() for hit in loaded.search("beta")] == [
        hit.to_dict() for hit in index.search("beta")
    ]
    with pytest.raises(FileExistsError):
        index.save(path)
    with pytest.raises(ValueError, match="size limit"):
        LexicalIndex.load(path, max_bytes=1)


def test_corrupt_payload_and_forged_derived_statistics_are_rejected():
    index = LexicalIndex([record("a", "alpha alpha")], strategy="bm25")
    payload = index.to_dict()
    payload["document_lengths"] = [99]
    with pytest.raises(ValueError, match="checksum"):
        LexicalIndex.from_dict(payload)
    payload["index_sha256"] = checksum(
        {key: value for key, value in payload.items() if key != "index_sha256"}
    )
    with pytest.raises(ValueError, match="statistics"):
        LexicalIndex.from_dict(payload)
    payload = index.to_dict()
    payload["records"][0]["text"] = "changed"
    payload["index_sha256"] = checksum(
        {key: value for key, value in payload.items() if key != "index_sha256"}
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        LexicalIndex.from_dict(payload)


def test_duplicate_json_keys_and_unknown_versions_fail_closed(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version":"1.0","schema_version":"2.0"}')
    with pytest.raises(ValueError, match="duplicate"):
        LexicalIndex.load(path)
    payload = LexicalIndex([]).to_dict()
    payload["schema_version"] = "unsupported"
    with pytest.raises(ValueError, match="version"):
        LexicalIndex.from_dict(payload)


def test_evidence_index_equal_score_regression_and_legacy_ranking():
    items = [
        EvidenceItem(
            evidence_id=name,
            evidence_type="method_note",
            source_path=name,
            source_label=name,
            text="alpha beta",
            content_hash=text_sha256("alpha beta"),
        )
        for name in ("b", "a")
    ]
    original = EvidenceIndex(items)
    assert [item.evidence_id for item in original.search("alpha")] == ["a", "b"]
    assert original.search("absent") == []
    with pytest.raises(ValueError, match="nonnegative"):
        original.search("alpha", -1)
