"""Explicit provider construction for budgeted research runs."""

from __future__ import annotations

import os
import re
from pathlib import Path

from manuscriptforge.llm.base import LLMProvider
from manuscriptforge.llm.budget import BudgetLedger
from manuscriptforge.llm.grounded import mock_grounded_provider
from manuscriptforge.llm.openai_provider import OpenAIProvider
from manuscriptforge.research.settings import ResearchSettings


def load_key_file(path: Path) -> bool:
    """Read an explicitly selected secret file without logging its contents."""
    try:
        content = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError("The explicitly selected API key file could not be read") from exc
    matches = re.findall(r"sk-[A-Za-z0-9_-]{20,}", content)
    if len(set(matches)) != 1:
        raise ValueError("The key file must contain one unambiguous OpenAI API key")
    os.environ["OPENAI_API_KEY"] = matches[0]
    return True


def load_configured_key(project_dir: Path) -> bool:
    """Load only the explicitly named API key; never print or serialize secrets."""
    if os.environ.get("OPENAI_API_KEY"):
        return True
    if os.environ.get("MANUSCRIPTFORGE_KEY_FILE"):
        return load_key_file(Path(os.environ["MANUSCRIPTFORGE_KEY_FILE"]))
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, "OPENAI_API_KEY")
            if isinstance(value, str) and value.strip():
                os.environ["OPENAI_API_KEY"] = value.strip()
                return True
        except OSError:
            pass
    path = Path(project_dir) / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name.strip() == "OPENAI_API_KEY":
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                if value:
                    os.environ["OPENAI_API_KEY"] = value
                    return True
    return False


def research_provider(settings: ResearchSettings) -> LLMProvider:
    if settings.provider == "mock":
        return mock_grounded_provider()
    ledger = BudgetLedger(Path(settings.budget_ledger_path or ""), settings.budget_limit_usd or "0")
    if settings.model not in ledger.prices:
        raise ValueError("The requested model has no verified price policy")
    return OpenAIProvider(
        model=settings.model,
        temperature=None,
        local_only=False,
        max_retries=0,
        timeout_seconds=settings.request_timeout_seconds,
        max_output_tokens=settings.max_output_tokens,
        max_input_bytes=200_000,
        reasoning_effort=settings.reasoning_effort,
        before_request=ledger.before_request,
        on_result=ledger.on_result,
    )
