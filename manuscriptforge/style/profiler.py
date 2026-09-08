from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from manuscriptforge.config import load_project_config
from manuscriptforge.ingest.style_ingest import build_style_chunks, ingest_style_corpus
from manuscriptforge.llm.base import LLMProvider
from manuscriptforge.models.style import StyleChunk, StyleProfile
from manuscriptforge.style.curation import apply_chunk_curation_to_profile_chunks
from manuscriptforge.style.document_extractors import (
    load_extraction_manifest,
    summarize_extractions,
)
from manuscriptforge.style.features import (
    ACADEMIC_VERBS,
    HEDGING_WORDS,
    PROFILE_SECTION_TYPES,
    citation_patterns,
    detected_style_features,
    first_person_count,
    limitation_phrases,
    normalize_section_type,
    passive_voice_ratio,
    punctuation_counts,
)
from manuscriptforge.style.style_guide import render_style_guide
from manuscriptforge.utils.io import write_json, write_jsonl, write_text
from manuscriptforge.utils.text import (
    common_ngrams,
    count_terms,
    distribution,
    find_transition_counts,
    split_paragraphs,
    split_sentences,
    word_tokens,
)

AVOIDED_PHRASES = ["proves", "guarantees", "validates clinically", "groundbreaking", "game-changing"]


def _section_distribution(chunks: list[StyleChunk]) -> dict[str, int]:
    return dict(Counter(chunk.section_type for chunk in chunks))


def _style_mode_distribution(chunks: list[StyleChunk]) -> dict[str, int]:
    return dict(Counter(chunk.style_mode for chunk in chunks))


def _extension_distribution(chunks: list[StyleChunk]) -> dict[str, int]:
    return dict(Counter(chunk.source_extension or Path(chunk.source_file).suffix.lower() for chunk in chunks))


def _style_mode_extension_distribution(chunks: list[StyleChunk]) -> dict[str, dict[str, int]]:
    distribution: dict[str, Counter[str]] = defaultdict(Counter)
    for chunk in chunks:
        extension = chunk.source_extension or Path(chunk.source_file).suffix.lower()
        distribution[chunk.style_mode][extension] += 1
    return {mode: dict(values) for mode, values in distribution.items()}


def _chunk_index(chunks: list[StyleChunk]) -> dict[str, Any]:
    return {
        "chunk_count": len(chunks),
        "section_distribution": _section_distribution(chunks),
        "style_mode_distribution": _style_mode_distribution(chunks),
        "source_extension_distribution": _extension_distribution(chunks),
        "style_mode_extension_distribution": _style_mode_extension_distribution(chunks),
        "source_files": sorted({chunk.source_file for chunk in chunks}),
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "source_file": chunk.source_file,
                "section_type": chunk.section_type,
                "style_mode": chunk.style_mode,
                "source_extension": chunk.source_extension,
                "source_asset_id": chunk.source_asset_id,
                "extracted_text_path": chunk.extracted_text_path,
                "extraction_status": chunk.extraction_status,
                "token_estimate": chunk.token_estimate,
                "sentence_count": chunk.sentence_count,
                "paragraph_count": chunk.paragraph_count,
                "content_hash": chunk.content_hash,
            }
            for chunk in chunks
        ],
    }


def _profile_payload(
    chunks: list[StyleChunk],
    section_type: str = "global",
) -> dict[str, Any]:
    text = "\n\n".join(chunk.text for chunk in chunks)
    sentences = split_sentences(text)
    paragraphs = split_paragraphs(text)
    tokens = word_tokens(text)
    sentence_lengths = [len(word_tokens(sentence)) for sentence in sentences]
    paragraph_lengths = [len(word_tokens(paragraph)) for paragraph in paragraphs]
    features = detected_style_features(text)
    limitation_style = limitation_phrases(sentences)
    passive_ratio = passive_voice_ratio(sentences)
    return {
        "section_type": section_type,
        "chunk_count": len(chunks),
        "source_files": sorted({chunk.source_file for chunk in chunks}),
        "source_extensions": _extension_distribution(chunks),
        "style_modes": sorted({chunk.style_mode for chunk in chunks}),
        "token_estimate": sum(chunk.token_estimate for chunk in chunks),
        "word_count": len(tokens),
        "sentence_count": len(sentences),
        "paragraph_count": len(paragraphs),
        "sentence_length_distribution": distribution(sentence_lengths),
        "paragraph_length_distribution": distribution(paragraph_lengths),
        "hedging_terms": count_terms(tokens, HEDGING_WORDS),
        "hedging_terms_per_1000_words": round(
            1000 * sum(count_terms(tokens, HEDGING_WORDS).values()) / max(len(tokens), 1), 2
        ),
        "transition_phrases": find_transition_counts(text),
        "common_verbs": count_terms(tokens, ACADEMIC_VERBS),
        "noun_phrase_patterns": features["noun_phrase_patterns"],
        "passive_voice_estimate": passive_ratio,
        "passive_voice_ratio": passive_ratio,
        "first_person_usage": first_person_count(tokens),
        "citation_integration_patterns": citation_patterns(text),
        "limitation_phrasing": limitation_style,
        "limitation_phrases": limitation_style,
        "opening_sentence_patterns": [sentence[:160] for sentence in sentences[:8]],
        "closing_sentence_patterns": [sentence[:160] for sentence in sentences[-8:]],
        "banned_or_avoided_phrases": AVOIDED_PHRASES,
        "representative_examples": [
            {
                "chunk_id": chunk.chunk_id,
                "source_file": chunk.source_file,
                "section_type": chunk.section_type,
                "style_mode": chunk.style_mode,
                "source_extension": chunk.source_extension,
                "extraction_status": chunk.extraction_status,
                "text": chunk.text[:1200],
            }
            for chunk in chunks[:10]
        ],
        "chunk_ids": [chunk.chunk_id for chunk in chunks],
        "punctuation_frequency": punctuation_counts(text),
        "common_unigrams": common_ngrams(tokens, 1, limit=15),
        "common_bigrams": common_ngrams(tokens, 2, limit=15),
        "common_trigrams": common_ngrams(tokens, 3, limit=12),
    }


def _section_profiles(chunks: list[StyleChunk]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[StyleChunk]] = defaultdict(list)
    for chunk in chunks:
        grouped[normalize_section_type(chunk.section_type)].append(chunk)
    section_types = sorted(set(PROFILE_SECTION_TYPES) | set(grouped))
    return {
        section_type: _profile_payload(grouped.get(section_type, []), section_type)
        for section_type in section_types
    }


def _examples_by_section(chunks: list[StyleChunk]) -> dict[str, list[str]]:
    examples: dict[str, list[str]] = defaultdict(list)
    for chunk in chunks:
        examples[chunk.section_type].append(chunk.text)
        examples[chunk.section_type.replace("_", " ").title()].append(chunk.text)
    return dict(examples)


def build_style_profile(
    project_dir: Path,
    run_dir: Path | None = None,
    samples: list[dict[str, Any]] | None = None,
    provider: LLMProvider | None = None,
) -> StyleProfile:
    config = load_project_config(project_dir)
    samples = samples if samples is not None else ingest_style_corpus(project_dir)
    chunks = build_style_chunks(project_dir, samples)
    chunks, curation_summary = apply_chunk_curation_to_profile_chunks(project_dir, chunks)
    corpus_files = sorted({chunk.source_file for chunk in chunks})
    active_style_mode = config.style.active_mode
    global_profile = _profile_payload(chunks, "global")
    section_features = _section_profiles(chunks)
    global_profile["sample_count"] = len(samples)
    global_profile["style_chunks"] = [chunk.model_dump(mode="json") for chunk in chunks]
    global_profile["style_chunk_index"] = _chunk_index(chunks)
    global_profile["section_distribution"] = _section_distribution(chunks)
    global_profile["style_mode_distribution"] = _style_mode_distribution(chunks)
    global_profile["source_extension_distribution"] = _extension_distribution(chunks)
    global_profile["style_mode_extension_distribution"] = _style_mode_extension_distribution(chunks)
    global_profile["active_style_mode"] = active_style_mode
    global_profile["section_controls"] = config.style.section_controls
    global_profile["style_extraction_summary"] = summarize_extractions(load_extraction_manifest(project_dir))
    global_profile["style_chunk_curation"] = curation_summary

    tokens = word_tokens("\n\n".join(chunk.text for chunk in chunks))
    citation_style = global_profile["citation_integration_patterns"]
    profile = StyleProfile(
        style_mode=active_style_mode,
        corpus_files=corpus_files,
        global_features=global_profile,
        section_features=section_features,
        preferred_phrases=common_ngrams(tokens, 3, limit=8),
        avoided_phrases=AVOIDED_PHRASES,
        hedging_profile={
            "terms": global_profile["hedging_terms"],
            "hedging_terms_per_1000_words": global_profile["hedging_terms_per_1000_words"],
        },
        transition_profile={"transition_counts": global_profile["transition_phrases"]},
        citation_style=citation_style,
        sentence_length_distribution=global_profile["sentence_length_distribution"],
        paragraph_length_distribution=global_profile["paragraph_length_distribution"],
        examples_by_section=_examples_by_section(chunks),
    )
    if provider is not None and chunks:
        all_text = "\n\n".join(chunk.text for chunk in chunks)
        profile.global_features["llm_style_note"] = provider.generate_text(
            "Summarize style cautiously from these examples:\n" + all_text[:4000]
        )
    if run_dir is not None:
        write_jsonl(run_dir / "style_chunks.jsonl", chunks)
        write_json(run_dir / "style_chunk_index.json", _chunk_index(chunks))
        write_json(run_dir / "global_style_profile.json", global_profile)
        for section_type in sorted(set(PROFILE_SECTION_TYPES) | set(section_features)):
            write_json(run_dir / f"{section_type}_style_profile.json", section_features[section_type])
        write_json(run_dir / "style_profile.json", profile)
        write_text(run_dir / "style_guide.md", render_style_guide(profile))
    return profile
