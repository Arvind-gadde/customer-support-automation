"""Cheap, deterministic input guardrails that run BEFORE any LLM call.

These are a first layer, not the whole defence. OpenAI moderation runs next
(llm/moderation.py), the output guard checks every answer on the way out, and the real security
boundary is that tools are scoped to the authenticated customer server-side
(see tools.py) - so even a successful injection cannot read someone else's data.
"""

from __future__ import annotations

import re

_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (all |any )?(previous|prior|above) (instructions|rules|prompts?)",
        r"disregard (the |your )?(system|previous) (prompt|instructions)",
        r"(reveal|print|show|repeat) (me )?(your|the) (system )?(prompt|instructions)",
        r"you are now (?!talking)",  # "you are now DAN..."
        r"\b(developer|god|jailbreak) mode\b",
        r"</?(system|assistant)>",
    )
]

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def detect_prompt_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _shingles(text: str, size: int) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def leaks_system_prompt(text: str, system_prompts: list[str], window: int = 8) -> bool:
    """True if the answer reproduces any `window`-word run of a system prompt.

    Eight consecutive words of our own instructions don't appear in a genuine
    answer by accident, but they do when a model is coaxed into reciting them.
    """
    answer = _shingles(text, window)
    return any(answer & _shingles(prompt, window) for prompt in system_prompts)


def redact_pii(text: str) -> str:
    """Redact PII before it reaches logs/traces. Luhn check avoids redacting order numbers."""

    def _card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group())
        return "[CARD]" if 13 <= len(digits) <= 19 and _luhn_valid(digits) else match.group()

    text = _CARD_CANDIDATE.sub(_card, text)
    text = _EMAIL.sub("[EMAIL]", text)
    return _PHONE.sub("[PHONE]", text)
