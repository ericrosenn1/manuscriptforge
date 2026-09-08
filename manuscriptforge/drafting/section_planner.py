from __future__ import annotations


def section_goal(section_name: str) -> str:
    goals = {
        "Introduction": "problem, gap, rationale, and objective",
        "Methods": "dataset, preprocessing, analysis, statistics, software, and reproducibility",
        "Results": "figure- and table-driven factual observations with minimal interpretation",
        "Discussion": "main finding, interpretation, relation to prior work, limitations, implications, and future work",
        "Limitations": "scope constraints, missing validation, missing methods details, and uncertainty",
        "Conclusion": "brief cautious take-home message",
    }
    return goals.get(section_name, "auditable manuscript content")
