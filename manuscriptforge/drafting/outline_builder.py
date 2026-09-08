from __future__ import annotations

from manuscriptforge.models.project import ProjectConfig


def build_outline(config: ProjectConfig) -> list[str]:
    if config.manuscript.include_sections:
        return config.manuscript.include_sections
    if config.article_type == "review_article":
        return ["Abstract", "Introduction", "Search Strategy", "Thematic Synthesis", "Gaps and Limitations", "Conclusion", "References"]
    return ["Abstract", "Introduction", "Methods", "Results", "Discussion", "Limitations", "Conclusion", "References"]
