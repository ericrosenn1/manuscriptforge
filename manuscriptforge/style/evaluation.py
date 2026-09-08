from __future__ import annotations

from pathlib import Path
from typing import Any

from manuscriptforge.ingest.style_ingest import build_style_chunks
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.io import write_jsonl
from manuscriptforge.utils.text import split_paragraphs, split_sentences


def _neutralize_sentence(sentence: str) -> str:
    lowered = sentence.strip()
    if not lowered:
        return "The result was evaluated using available information."
    return (
        "The result was evaluated using available information, and additional context may be needed "
        "before interpretation."
    )


def _generic_distractor(section_type: str) -> str:
    if section_type == "methods":
        return "The methods were comprehensive and performed according to standard procedures."
    if section_type == "results":
        return "The results clearly demonstrate an important and robust effect."
    if section_type == "limitations":
        return "There were no meaningful limitations that affect interpretation."
    return "This section highlights important findings and their broader impact."


def _rank_options(sentence: str, section_type: str) -> list[str]:
    return [
        sentence,
        _neutralize_sentence(sentence),
        _generic_distractor(section_type),
    ]


def build_style_evaluation_set(project_dir: Path, run_dir: Path) -> dict[str, int]:
    chunks = build_style_chunks(project_dir)
    pair_rows: list[dict[str, Any]] = []
    rewrite_rows: list[dict[str, Any]] = []
    rank_rows: list[dict[str, Any]] = []

    for chunk in chunks:
        sentences = split_sentences(chunk.text)
        paragraphs = split_paragraphs(chunk.text)
        if sentences:
            sentence = sentences[0]
            pair_rows.append(
                {
                    "example_id": stable_id("seval_pair", f"{chunk.chunk_id}:{sentence}"),
                    "task": "true_user_sentence_vs_distractor",
                    "section_type": chunk.section_type,
                    "style_mode": chunk.style_mode,
                    "source_chunk_id": chunk.chunk_id,
                    "positive": sentence,
                    "negative": _generic_distractor(chunk.section_type),
                    "private_writing": True,
                }
            )
            rank_rows.append(
                {
                    "example_id": stable_id("seval_rank", f"{chunk.chunk_id}:{sentence}"),
                    "task": "rank_user_style_options",
                    "section_type": chunk.section_type,
                    "style_mode": chunk.style_mode,
                    "source_chunk_id": chunk.chunk_id,
                    "options": _rank_options(sentence, chunk.section_type),
                    "preferred_option_index": 0,
                    "private_writing": True,
                }
            )
            rank_rows.append(
                {
                    "example_id": stable_id("seval_section", f"{chunk.chunk_id}:section"),
                    "task": "section_classification",
                    "section_type": chunk.section_type,
                    "style_mode": chunk.style_mode,
                    "source_chunk_id": chunk.chunk_id,
                    "text": sentence,
                    "candidate_sections": ["abstract", "methods", "results", "discussion", "limitations"],
                    "private_writing": True,
                }
            )
            pair_rows.append(
                {
                    "example_id": stable_id("seval_hedge", f"{chunk.chunk_id}:{sentence}"),
                    "task": "hedge_level_comparison",
                    "section_type": chunk.section_type,
                    "style_mode": chunk.style_mode,
                    "source_chunk_id": chunk.chunk_id,
                    "positive": sentence,
                    "negative": sentence.replace(" may ", " will ").replace(" suggest", " prove"),
                    "private_writing": True,
                }
            )
        if paragraphs:
            paragraph = paragraphs[0]
            rewrite_rows.append(
                {
                    "example_id": stable_id("seval_rewrite", f"{chunk.chunk_id}:{paragraph[:80]}"),
                    "task": "neutral_to_user_style_rewrite",
                    "section_type": chunk.section_type,
                    "style_mode": chunk.style_mode,
                    "source_chunk_id": chunk.chunk_id,
                    "prompt": "Rewrite the neutral paragraph in the user's manuscript style.",
                    "neutral_paragraph": _neutralize_sentence(paragraph),
                    "target_user_paragraph": paragraph,
                    "private_writing": True,
                }
            )

    write_jsonl(run_dir / "style_eval_pairs.jsonl", pair_rows)
    write_jsonl(run_dir / "style_eval_rewrite_prompts.jsonl", rewrite_rows)
    write_jsonl(run_dir / "style_eval_rank_questions.jsonl", rank_rows)
    return {
        "style_eval_pairs": len(pair_rows),
        "style_eval_rewrite_prompts": len(rewrite_rows),
        "style_eval_rank_questions": len(rank_rows),
        "style_chunks": len(chunks),
    }
