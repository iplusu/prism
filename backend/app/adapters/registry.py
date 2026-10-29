from __future__ import annotations

from ..config import get_settings
from .antigravity import AntigravityAdapter
from .base import Adapter
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .demo import DemoAdapter

_REAL: dict[str, Adapter] = {"codex": CodexAdapter(), "claude": ClaudeCodeAdapter(), "antigravity": AntigravityAdapter()}
_DEMO = DemoAdapter()
_OVERRIDES: dict[str, Adapter] = {}  # tests register fake CLIs here


def adapters() -> dict[str, Adapter]:
    out = dict(_REAL)
    out.update(_OVERRIDES)
    if get_settings().demo_mode:
        out["demo"] = _DEMO
    return out


def get_adapter(name: str) -> Adapter | None:
    return adapters().get(name)


def register_override(name: str, adapter: Adapter) -> None:
    _OVERRIDES[name] = adapter


def clear_overrides() -> None:
    _OVERRIDES.clear()
    Adapter._health_cache.clear()
