"""Schemas for orchestrator output and the task contract.

The LLM proposes; the server decides. Model output is validated against these
models (unknown fields are rejected). Budgets (timeouts, attempts) and the
executor allow-list are filled in or clamped by the server, and nothing in a
plan can carry a shell command.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..security.paths import PathError, is_protected, normalize_rel_path, validate_scope_pattern

EXECUTORS = ("codex", "claude", "antigravity")
TaskKind = Literal["edit_text", "fix_build", "bibliography", "restructure", "figures_tables", "analysis", "other"]
AcceptanceCheck = Literal[
    "compiles", "no_new_undefined_references", "no_new_undefined_citations", "preserve_citations",
    "preserve_labels", "preserve_numbers", "preserve_equations", "only_write_scope",
]
DEFAULT_CHECKS: list[str] = ["compiles", "only_write_scope", "no_new_undefined_references", "no_new_undefined_citations"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Edit(Strict):
    path: str = Field(max_length=400)
    find: str = Field(min_length=1, max_length=20000, description="exact existing text, must occur exactly once")
    replace: str = Field(max_length=40000)

    @field_validator("path")
    @classmethod
    def _p(cls, v: str) -> str:
        v = normalize_rel_path(v)
        if is_protected(v):
            raise ValueError(f"{v} is a protected file")
        return v


class DelegationRequest(Strict):
    """What the model asks for when it delegates; the server turns this into a
    TaskContract."""

    goal: str = Field(min_length=1, max_length=4000)
    task_kind: TaskKind = "other"
    executor: Literal["auto", "codex", "claude", "antigravity"] = "auto"
    read_scope: list[str] = Field(default_factory=lambda: ["**"], max_length=50)
    write_scope: list[str] = Field(min_length=1, max_length=50)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    acceptance_checks: list[AcceptanceCheck] = Field(default_factory=lambda: list(DEFAULT_CHECKS))

    @field_validator("read_scope", "write_scope")
    @classmethod
    def _scopes(cls, v: list[str]) -> list[str]:
        out = []
        for pat in v:
            try:
                out.append(validate_scope_pattern(pat))
            except PathError as e:
                raise ValueError(str(e)) from e
        return out

    @field_validator("constraints")
    @classmethod
    def _c(cls, v: list[str]) -> list[str]:
        return [c[:500] for c in v]


class Plan(Strict):
    """Single structured decision returned by the orchestrator model."""

    action: Literal["answer", "edit", "delegate"]
    message: str = Field(default="", max_length=20000, description="answer text or a short explanation to the user")
    edits: list[Edit] = Field(default_factory=list, max_length=40)
    delegation: DelegationRequest | None = None
    summary: str = Field(default="", max_length=600, description="one-line summary of the proposed change")


class TaskContract(Strict):
    task_id: str
    project_id: str
    base_revision: str
    goal: str
    task_kind: str
    executor: Literal["codex", "claude", "antigravity", "llm", "demo"]
    read_scope: list[str]
    write_scope: list[str]
    constraints: list[str]
    acceptance_checks: list[str]
    timeout_seconds: int
    max_attempts: int
    task_deadline_seconds: int


PLAN_JSON_SCHEMA = Plan.model_json_schema()
