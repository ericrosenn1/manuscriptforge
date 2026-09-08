from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from manuscriptforge.models.audit import AuditFinding
from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.models.style import StyleProfile
from manuscriptforge.style.memory import update_style_memory
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.io import write_json, write_text


def _representative_context(profile: StyleProfile | None, section_type: str) -> str:
    if profile is None:
        return ""
    section_profile = profile.section_features.get(section_type, {})
    examples = section_profile.get("representative_examples", [])
    if examples and isinstance(examples[0], dict):
        return str(examples[0].get("text", ""))[:360]
    values = profile.examples_by_section.get(section_type, [])
    return values[0][:360] if values else ""


def _question(
    question_type: str,
    section_type: str,
    prompt: str,
    options: list[str],
    source_context: str,
    why: str,
    target: str,
) -> dict[str, Any]:
    return {
        "question_id": stable_id("sq", f"{question_type}:{section_type}:{prompt}:{source_context}")[:14],
        "question_type": question_type,
        "section_type": section_type,
        "prompt": prompt,
        "options": options,
        "source_context": source_context,
        "why_this_question_was_asked": why,
        "model_feature_target": target,
    }


def generate_style_questions(
    profile: StyleProfile | None = None,
    manuscript: Manuscript | None = None,
    findings: list[AuditFinding] | None = None,
) -> list[dict[str, Any]]:
    preferred_phrase = "is consistent with"
    hedge_options = ["suggests", "may indicate", "is consistent with", "proves"]
    transition_options = ["Together", "In contrast", "Notably"]
    limitation_options = [
        "Important limitations include sample size and the need for independent validation.",
        "The main limitation is that the analysis is preliminary.",
        "These results are definitive and require no additional validation.",
    ]
    citation_options = [
        "The association has been reported in prior work [cit_example].",
        "Prior work [cit_example] reported a related association.",
        "[cit_example] reported a related association in prior work.",
    ]
    if profile is not None:
        if profile.preferred_phrases:
            preferred_phrase = profile.preferred_phrases[0]
        hedges = list(profile.hedging_profile.get("terms", {}).keys())
        if hedges:
            hedge_options = list(dict.fromkeys(hedges + hedge_options))[:5]
        transitions = list(profile.transition_profile.get("transition_counts", {}).keys())
        if transitions:
            transition_options = list(dict.fromkeys(transitions + transition_options))[:5]
        limitation_phrases = profile.global_features.get("limitation_phrasing", [])
        if limitation_phrases:
            limitation_options = list(dict.fromkeys([str(item) for item in limitation_phrases[:2]] + limitation_options))

    audit_context = ""
    for finding in findings or []:
        if finding.category in {"overclaiming", "style"}:
            audit_context = finding.message
            break
    manuscript_context = ""
    if manuscript is not None:
        manuscript_context = manuscript.abstract[:360]

    results_context = _representative_context(profile, "results") or manuscript_context
    discussion_context = _representative_context(profile, "discussion") or audit_context
    methods_context = _representative_context(profile, "methods")
    limitations_context = _representative_context(profile, "limitations")
    introduction_context = _representative_context(profile, "introduction")

    questions = [
        _question(
            "pairwise_preference",
            "results",
            "Which sentence is closest to your Results style?",
            [
                "The results demonstrate a robust biomarker effect.",
                "The results are consistent with a biomarker-associated pattern.",
            ],
            results_context,
            "The Results section should preserve your boundary between observation and interpretation.",
            "results_restraint",
        ),
        _question(
            "rank_3_options",
            "discussion",
            "Rank these Discussion openers from most to least like your style.",
            [
                "Together, these observations suggest a focused signal that may warrant follow-up.",
                "These findings prove that the signal is clinically actionable.",
                "Overall, the results are interesting and important.",
            ],
            discussion_context,
            "Discussion phrasing controls how strongly the draft interprets evidence.",
            "causal_language",
        ),
        _question(
            "rewrite_this_sentence",
            "discussion",
            "Rewrite this sentence as you would write it: These findings prove clinical utility.",
            [],
            audit_context or discussion_context,
            "Free-text rewrites capture preferred phrasing that fixed choices cannot express.",
            "causal_language",
        ),
        _question(
            "choose_hedge_strength",
            "results",
            "Choose the best hedge level for an indirect or weakly supported interpretation.",
            hedge_options,
            results_context,
            "The corpus and audits indicate hedge strength is a key style-control setting.",
            "hedging",
        ),
        _question(
            "choose_best_transition",
            "discussion",
            "Choose the transition that best fits your Discussion style.",
            transition_options,
            discussion_context,
            "Transitions are learned from prior writing but still need author calibration.",
            "transition_style",
        ),
        _question(
            "choose_least_acceptable_option",
            "all",
            "Which option is least acceptable in your manuscript voice?",
            ["proves", "guarantees", "validates clinically", preferred_phrase],
            audit_context or results_context,
            "Avoided phrases help the draft stay away from overclaiming and disliked generic wording.",
            "causal_language",
        ),
        _question(
            "choose_citation_integration_style",
            "introduction",
            "Choose the citation integration pattern closest to your style.",
            citation_options,
            introduction_context,
            "Citation placement affects whether prose reads like your prior manuscript work.",
            "citation_style",
        ),
        _question(
            "choose_limitation_phrasing",
            "limitations",
            "Choose the limitation phrasing closest to your style.",
            limitation_options,
            limitations_context,
            "Limitations should match your preferred balance of specificity and restraint.",
            "limitation_style",
        ),
        _question(
            "rank_3_options",
            "methods",
            "Rank these Methods sentences by how close they are to your precision level.",
            [
                "The analysis used versioned scripts and inspectable intermediate artifacts.",
                "The analysis was performed using standard methods.",
                "The workflow was robust and comprehensive.",
            ],
            methods_context,
            "Methods prose needs section-specific precision rather than generic manuscript phrasing.",
            "methods_precision",
        ),
    ]
    return questions


def render_questions_markdown(questions: list[dict[str, Any]]) -> str:
    lines = ["# Style Calibration Questions", ""]
    for index, question in enumerate(questions, start=1):
        lines.append(f"## {index}. {question['question_type'].replace('_', ' ').title()}")
        lines.append(f"**Section:** {question['section_type']}")
        lines.append(question["prompt"])
        if question.get("source_context"):
            lines.append("")
            lines.append("> " + str(question["source_context"]).replace("\n", " ")[:420])
        lines.append("")
        lines.append(f"_Why this was asked:_ {question['why_this_question_was_asked']}")
        lines.append(f"_Feature target:_ {question['model_feature_target']}")
        for option in question.get("options", []):
            lines.append(f"- [ ] {option}")
        lines.append("")
    return "\n".join(lines)


def write_style_questions(
    project_dir: Path,
    run_dir: Path,
    profile: StyleProfile,
    manuscript: Manuscript | None = None,
    findings: list[AuditFinding] | None = None,
) -> StyleProfile:
    questions = generate_style_questions(profile, manuscript, findings)
    profile.preference_training_examples.extend(questions)
    answers_path = project_dir / "style_preferences.jsonl"
    with answers_path.open("a", encoding="utf-8", newline="\n") as handle:
        for question in questions:
            handle.write(json.dumps({"question": question, "answer": None}) + "\n")
    memory = update_style_memory(project_dir, profile)
    write_text(run_dir / "style_questions.md", render_questions_markdown(questions))
    write_json(run_dir / "style_questions.json", questions)
    write_json(run_dir / "style_memory.json", memory)
    write_json(run_dir / "style_profile_updated.json", profile)
    return profile
