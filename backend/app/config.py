"""Runtime configuration. Every value comes from the environment (or .env);
no endpoints or credentials are hard-coded here."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # PRISM_ENV_FILE is the same override the scripts use; tests point it at os.devnull.
    model_config = SettingsConfigDict(env_file=os.environ.get("PRISM_ENV_FILE") or str(REPO_ROOT / ".env"), extra="ignore")

    # --- core -------------------------------------------------------------
    database_url: str = Field(default=f"sqlite:///{REPO_ROOT / 'var' / 'prism.db'}", alias="DATABASE_URL")
    data_dir: Path = Field(default=REPO_ROOT / "var" / "data", alias="PRISM_DATA_DIR")
    secret_key: str = Field(default="", alias="PRISM_SECRET_KEY")
    cookie_secure: bool = Field(default=False, alias="PRISM_COOKIE_SECURE")
    session_hours: int = Field(default=72, alias="PRISM_SESSION_HOURS")
    admin_username: str = Field(default="", alias="PRISM_ADMIN_USERNAME")
    admin_password: str = Field(default="", alias="PRISM_ADMIN_PASSWORD")
    allow_signup: bool = Field(default=False, alias="PRISM_ALLOW_SIGNUP")
    demo_mode: bool = Field(default=False, alias="DEMO_MODE")
    frontend_dist: Path = Field(default=REPO_ROOT / "frontend" / "dist", alias="PRISM_FRONTEND_DIST")

    # --- orchestrator LLM (runs on a separate inference server, e.g. a5000) ----
    # The web server never hosts the model; the backend calls this API. Nothing
    # about the server (port, model id, API format, context length) is guessed.
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")  # openai (OpenAI-compatible)
    llm_base_url: str = Field(default="", alias="LLM_BASE_URL")
    llm_model: str = Field(default="", alias="LLM_MODEL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_timeout_seconds: float = Field(default=120.0, alias="LLM_TIMEOUT_SECONDS")
    llm_connect_timeout_seconds: float = Field(default=10.0, alias="LLM_CONNECT_TIMEOUT_SECONDS")
    # Characters of project context sent per request. Set it to fit the
    # served model's real context window (not inferred automatically).
    llm_context_chars: int = Field(default=24000, alias="LLM_CONTEXT_CHARS")
    llm_max_output_tokens: int = Field(default=4096, alias="LLM_MAX_OUTPUT_TOKENS")
    # Inline editor completion is best-effort: a slow answer is useless, so it gets its own short limit.
    llm_completion_timeout_seconds: float = Field(default=20.0, alias="LLM_COMPLETION_TIMEOUT_SECONDS")
    # auto | tools | json  (auto = use the capability found by the connection test)
    llm_planning_mode: str = Field(default="auto", alias="LLM_PLANNING_MODE")

    # --- collaboration service ----------------------------------------------
    collab_enabled: bool = Field(default=False, alias="COLLAB_ENABLED")
    collab_internal_url: str = Field(default="http://127.0.0.1:1234", alias="COLLAB_INTERNAL_URL")
    collab_public_url: str = Field(default="", alias="COLLAB_PUBLIC_URL")
    collab_shared_secret: str = Field(default="", alias="COLLAB_SHARED_SECRET")

    # --- compilation sandbox ------------------------------------------------
    tex_bin_dir: str = Field(default="", alias="TEX_BIN_DIR")
    # Extra read-only roots that must be visible inside the sandbox (TeX tree).
    tex_root: str = Field(default="", alias="TEX_ROOT")
    compile_sandbox: str = Field(default="bwrap", alias="COMPILE_SANDBOX")  # bwrap | none
    allow_unsandboxed_compile: bool = Field(default=False, alias="ALLOW_UNSANDBOXED_COMPILE")
    compile_timeout_seconds: int = Field(default=180, alias="COMPILE_TIMEOUT_SECONDS")
    compile_memory_mb: int = Field(default=2048, alias="COMPILE_MEMORY_MB")
    compile_cpu_seconds: int = Field(default=300, alias="COMPILE_CPU_SECONDS")
    compile_max_processes: int = Field(default=4096, alias="COMPILE_MAX_PROCESSES")
    compile_max_output_mb: int = Field(default=512, alias="COMPILE_MAX_OUTPUT_MB")

    # --- external agent tools -----------------------------------------------
    tool_sandbox: str = Field(default="bwrap", alias="TOOL_SANDBOX")  # bwrap | none
    allow_unsandboxed_tools: bool = Field(default=False, alias="ALLOW_UNSANDBOXED_TOOLS")
    tool_extra_ro_binds: str = Field(default="", alias="TOOL_EXTRA_RO_BINDS")  # ':'-separated
    codex_bin: str = Field(default="codex", alias="CODEX_BIN")
    codex_home: str = Field(default="", alias="CODEX_HOME_DIR")
    codex_model: str = Field(default="", alias="CODEX_MODEL")
    claude_bin: str = Field(default="claude", alias="CLAUDE_BIN")
    claude_config_dir: str = Field(default="", alias="CLAUDE_CONFIG_DIR_FOR_TOOL")
    claude_model: str = Field(default="", alias="CLAUDE_MODEL")
    claude_max_budget_usd: float = Field(default=0.0, alias="CLAUDE_MAX_BUDGET_USD")
    agy_bin: str = Field(default="agy", alias="AGY_BIN")
    agy_home: str = Field(default="", alias="AGY_HOME_DIR")
    agy_model: str = Field(default="", alias="AGY_MODEL")
    # Secrets forwarded only to the matching tool process (never logged).
    openai_api_key_for_tool: str = Field(default="", alias="CODEX_OPENAI_API_KEY")
    anthropic_api_key_for_tool: str = Field(default="", alias="CLAUDE_ANTHROPIC_API_KEY")
    gemini_api_key_for_tool: str = Field(default="", alias="AGY_GEMINI_API_KEY")

    agent_run_timeout_seconds: int = Field(default=600, alias="AGENT_RUN_TIMEOUT_SECONDS")
    agent_task_timeout_seconds: int = Field(default=1500, alias="AGENT_TASK_TIMEOUT_SECONDS")
    agent_max_attempts: int = Field(default=2, alias="AGENT_MAX_ATTEMPTS")
    agent_recent_failure_minutes: int = Field(default=30, alias="AGENT_RECENT_FAILURE_MINUTES")
    # Default ordering when several tools could do a job. This is a configurable
    # preference, not a quality ranking.
    agent_default_order: str = Field(default="codex,claude,antigravity", alias="AGENT_DEFAULT_ORDER")

    # --- import limits ---------------------------------------------------------
    max_upload_mb: int = Field(default=100, alias="PRISM_MAX_UPLOAD_MB")
    max_unzipped_mb: int = Field(default=500, alias="PRISM_MAX_UNZIPPED_MB")
    max_zip_entries: int = Field(default=5000, alias="PRISM_MAX_ZIP_ENTRIES")
    max_text_file_mb: int = Field(default=4, alias="PRISM_MAX_TEXT_FILE_MB")

    worker_poll_seconds: float = Field(default=0.5, alias="WORKER_POLL_SECONDS")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    def ensure_dirs(self) -> None:
        for sub in ("projects", "builds", "tasks", "proposals", "tool-homes"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.database_url.startswith("sqlite"):
        Path(s.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    s.data_dir = Path(s.data_dir).resolve()
    s.ensure_dirs()
    if not s.secret_key:
        # Development fallback: a per-install random key persisted in the data dir.
        key_file = s.data_dir / ".secret_key"
        if not key_file.exists():
            import secrets

            key_file.write_text(secrets.token_hex(32))
            key_file.chmod(0o600)
        s.secret_key = key_file.read_text().strip()
    return s
