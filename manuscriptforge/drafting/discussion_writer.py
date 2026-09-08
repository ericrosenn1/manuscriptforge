from __future__ import annotations

from manuscriptforge.drafting.section_writer import write_generic_section
from manuscriptforge.models.manuscript import ManuscriptSection
from manuscriptforge.pipeline.run_context import ManuscriptContext


def write_discussion(context: ManuscriptContext) -> ManuscriptSection:
    return write_generic_section(context, "Discussion")
