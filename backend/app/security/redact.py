"""Remove secrets from text before it is logged or shown in the UI."""

from __future__ import annotations

import re

from ..config import get_settings

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\"?\s*[:=]\s*\"?)[^\s\"',}]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
]


def _known_secrets() -> list[str]:
    s = get_settings()
    vals = [
        s.llm_api_key, s.secret_key, s.collab_shared_secret, s.openai_api_key_for_tool,
        s.anthropic_api_key_for_tool, s.gemini_api_key_for_tool, s.admin_password,
    ]
    return [v for v in vals if v and len(v) >= 6]


def redact(text: str) -> str:
    if not text:
        return text
    for v in _known_secrets():
        text = text.replace(v, "[REDACTED]")
    for rx in _PATTERNS:
        if rx.groups:
            text = rx.sub(lambda m: m.group(1) + "[REDACTED]", text)
        else:
            text = rx.sub("[REDACTED]", text)
    return text
