SAFETY_RULES = """
Rules:
- Do not invent data.
- Do not invent citations.
- Do not use unsupported causal claims.
- Use only provided evidence.
- Flag missing information.
- Preserve uncertainty.
- Separate Results from interpretation.
- Match user style using the style guide and examples, but do not copy long passages from prior writing.
"""

STYLE_PROFILE_PROMPT = SAFETY_RULES + "\nSummarize style patterns from the provided corpus chunk."
CLAIM_EXTRACTION_PROMPT = SAFETY_RULES + "\nExtract candidate claims as JSON."
CLAIM_STRENGTH_PROMPT = SAFETY_RULES + "\nClassify support strength for each claim."
OUTLINE_PROMPT = SAFETY_RULES + "\nCreate an auditable manuscript outline."
SECTION_DRAFT_PROMPT = SAFETY_RULES + "\nDraft only the requested section using provided claims."
CITATION_MAPPING_PROMPT = SAFETY_RULES + "\nMap claims to existing citation records only."
OVERCLAIMING_AUDIT_PROMPT = SAFETY_RULES + "\nFlag unsupported causal or excessive language."
REVIEWER_SIM_PROMPT = SAFETY_RULES + "\nWrite a constructive reviewer-style critique."
REVISION_QUESTIONS_PROMPT = SAFETY_RULES + "\nGenerate targeted revision questions."
AI_DISCLOSURE_PROMPT = SAFETY_RULES + "\nDraft a conservative AI-use disclosure."
