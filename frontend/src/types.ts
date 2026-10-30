// Shapes returned by the Prism backend (see backend/app/routers/*.py).

export type Role = "owner" | "editor" | "viewer";

export interface User {
  id: string;
  username: string;
  display_name: string;
  is_admin: boolean;
}

export interface AuthConfig {
  allow_signup: boolean;
  demo_mode: boolean;
}

export type BuildStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "blocked";

export interface Diagnostic {
  severity: "error" | "warning" | "info" | string;
  file: string | null;
  line: number | null;
  message: string;
}

export interface Build {
  id: string;
  project_id: string;
  kind: "main" | "proposal" | "base" | string;
  status: BuildStatus;
  revision: string;
  source_hash: string;
  engine: string;
  main_file: string;
  isolation: string;
  exit_code: number | null;
  diagnostics: Diagnostic[];
  has_pdf: boolean;
  error: string;
  created_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  proposal_id: string | null;
  log?: string;
  queued_behind?: string | null;
  /** LaTeX support files (e.g. titlesec.sty) the failed build could not find. */
  missing_files?: string[];
}

export type Executor = "codex" | "claude" | "antigravity";
export type TaskMode = "auto" | "llm" | Executor | "demo";

export interface AgentPolicy {
  allowed_executors: string[];
  default_executor: string;
  preference_order: string[];
  routing: Record<string, string[]>;
  allow_external: boolean;
  run_timeout_seconds: number;
  task_timeout_seconds: number;
  max_attempts: number;
}

export interface Member {
  user: User;
  role: Role;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  main_file: string;
  engine: "pdflatex" | "xelatex" | "lualatex" | string;
  role: Role | null;
  created_at: string;
  updated_at: string;
  last_build: Build | null;
  last_success_build: Build | null;
  active_tasks: number;
  open_proposals: number;
  policy?: AgentPolicy;
  members?: Member[];
  import?: { files: number; skipped: { path: string; reason: string }[]; stripped_prefix: string | null };
}

export interface ActiveTaskRef {
  id: string;
  status: TaskStatus;
  mode: TaskMode;
  executor: string;
}

export interface ProjectStatus {
  current_hash: string;
  live_error: string;
  last_build: Build | null;
  last_success_build: Build | null;
  pdf_stale: boolean;
  active_tasks: ActiveTaskRef[];
  open_proposals: number;
}

export interface FileEntry {
  path: string;
  size: number;
  is_text: boolean;
}

export interface FileContent {
  path: string;
  text: string;
  sha: string;
}

export interface OutlineSection {
  file: string;
  line: number;
  level: string;
  title: string;
}

export interface Outline {
  sections: OutlineSection[];
  labels: { label: string; file: string; line: number }[];
  bib: { key: string; type: string; file: string; title: string }[];
}

export interface TemplateInfo {
  id: string;
  label: string;
  description: string;
  source: string;
}

export interface PaperHit {
  source: "arxiv";
  id: string;
  url: string;
  title: string;
  authors: string[];
  year: string;
  summary: string;
  category: string;
}

export interface ReferenceImport {
  key: string;
  file: string;
  added: boolean;
  entry?: string;
}

export interface ReviewIssue {
  kind: string;
  severity: "error" | "warning" | "info";
  path: string;
  line: number | null;
  message: string;
}

export interface ReviewReport {
  issues: ReviewIssue[];
  counts: { error: number; warning: number; info: number };
}

export interface SearchResult {
  path: string;
  line: number;
  col: number;
  text: string;
}

export type TaskStatus =
  | "queued"
  | "planning"
  | "running"
  | "verifying"
  | "needs_review"
  | "completed"
  | "failed"
  | "cancelled";

export const ACTIVE_TASK_STATUSES: TaskStatus[] = ["queued", "planning", "running", "verifying"];

export interface TaskContextSpec {
  scope: "selection" | "file" | "project";
  path?: string | null;
  selection?: { from_line: number; to_line: number; text: string } | null;
  /** Analysis request: the server lets it answer only, never edit or delegate. */
  answer_only?: boolean;
}

export interface Task {
  id: string;
  project_id: string;
  status: TaskStatus;
  mode: TaskMode;
  request: string;
  context_spec: TaskContextSpec | null;
  contract: Record<string, unknown> | null;
  plan_source: string;
  executor_used: string;
  base_revision: string;
  proposal_id: string | null;
  error: string;
  demo: boolean;
  retry_of: string | null;
  cancel_requested: boolean;
  created_at: string;
  updated_at: string;
}

export interface TaskEvent {
  id: number;
  task_id: string;
  ts: string;
  type: string;
  message: string;
  data: Record<string, unknown>;
}

export interface ToolRun {
  id: string;
  executor: string;
  attempt: number;
  status: string;
  exit_code: number | null;
  session_id: string;
  isolation: string;
  summary: string;
  changed_files: unknown[];
  violations: { path?: string; reason?: string }[];
  started_at: string;
  finished_at: string | null;
}

export interface TaskDetail extends Task {
  tool_runs: ToolRun[];
  events: TaskEvent[];
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  task_id: string | null;
  meta: Record<string, unknown>;
  created_at: string;
}

export type ProposalStatus =
  | "verifying"
  | "ready"
  | "conflict"
  | "applying"
  | "applied"
  | "rejected"
  | "failed"
  | "superseded";

export interface ProposalFile {
  path: string;
  change: "added" | "deleted" | "modified";
  binary: boolean;
  base_sha: string | null;
  new_sha: string | null;
  added?: number;
  removed?: number;
}

export interface DiffLine {
  t: " " | "-" | "+";
  s: string;
}

export interface Hunk {
  id: number;
  kind: string;
  base_start: number;
  base_end: number;
  new_start: number;
  new_end: number;
  context_before: number;
  lines: DiffLine[];
}

export interface Check {
  check: string;
  status: "passed" | "failed" | "not_evaluated" | string;
  detail: string;
}

export interface CounterDiff {
  removed: string[];
  added: string[];
  removed_count: number;
  added_count: number;
}

export interface ProposalReport {
  checks_requested?: string[];
  scope_ok?: boolean;
  auto_apply?: boolean;
  tool_message?: string;
  checks?: Check[];
  analysis?: {
    citations: {
      added: string[];
      removed: string[];
      bib_entries_added: string[];
      bib_entries_removed: string[];
      new_undefined: string[];
      undefined_total: string[];
    };
    labels: { added: string[]; removed: string[]; new_dangling_refs: string[]; dangling_total: string[] };
    numbers: Record<string, CounterDiff>;
    equations: Record<string, CounterDiff>;
  };
  build?: {
    candidate: string;
    candidate_isolation: string;
    base: string;
    base_build_id: string;
    warnings_base: Record<string, number>;
    warnings_candidate: Record<string, number>;
    blocked_reason: string;
  };
  limitations?: string[];
  verified_at?: string;
  [k: string]: unknown;
}

export interface Conflict {
  path: string;
  reason: string;
  merged: string | null;
  current: string | null;
  proposal: string | null;
}

export interface Proposal {
  id: string;
  project_id: string;
  task_id: string | null;
  parent_id: string | null;
  origin: string;
  base_revision: string;
  status: ProposalStatus;
  title: string;
  summary: string;
  executor: string;
  files: ProposalFile[];
  error: string;
  demo: boolean;
  applied_revision: string;
  candidate_build_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  checks: Check[];
}

export interface ProposalDetail extends Proposal {
  report: ProposalReport;
  conflicts: Conflict[];
  candidate_build: Build | null;
  file_diffs: (ProposalFile & { hunks?: Hunk[] })[];
}

export interface Version {
  revision: string;
  author: string;
  date: string;
  message: string;
  labels: string[];
}

export interface VersionDiff {
  revision: string;
  files: { path: string; change: "added" | "deleted" | "modified"; hunks?: Hunk[] }[];
}

export interface CollabToken {
  enabled: boolean;
  url?: string;
  room?: string;
  token?: string;
  readonly?: boolean;
}

export interface ToolHealth {
  name: string;
  label: string;
  status: "available" | "unavailable" | "needs_auth" | "incompatible" | "error" | string;
  installed: boolean;
  version: string;
  auth: string;
  detail: string;
  checked_at: number;
  capabilities: Record<string, unknown>;
}

export interface LlmTestStep {
  name: string;
  status: "passed" | "failed" | "skipped" | string;
  detail: string;
  duration_ms: number | null;
}

export interface LlmTestReport {
  base_url: string;
  model: string;
  provider: string;
  tested_at: string;
  steps: LlmTestStep[];
  capabilities: {
    tool_calling?: boolean;
    json_object?: boolean;
    json_schema?: boolean;
    streaming?: boolean;
    planning_mode?: string;
  };
  ok: boolean;
  models?: string[];
  context_reported?: unknown;
  served_model_id?: string;
  error_kind?: string;
}

export interface SystemStatus {
  demo_mode: boolean;
  llm: {
    provider: string;
    base_url: string;
    model: string;
    api_key_set: boolean;
    configured: boolean;
    timeout_seconds: number;
    context_chars: number;
    planning_mode_setting: string;
  };
  llm_last_test: LlmTestReport | null;
  tex: { available: boolean; detail: string; bin_dir?: string };
  compile_sandbox: { mode: string; isolated: boolean; detail: string };
  tool_sandbox: { mode: string; bwrap: boolean; detail: string; allow_unsandboxed: boolean };
  collab: { enabled: boolean; status: string };
  tools: ToolHealth[];
  limits: {
    run_timeout_seconds: number;
    task_timeout_seconds: number;
    max_attempts: number;
    compile_timeout_seconds: number;
    compile_memory_mb: number;
  };
  agent_default_order: string;
}

/** A rectangle on a PDF page in PDF points, origin at the page's top-left. */
export interface PdfMark {
  page: number;
  x: number;
  y: number;
  w: number;
  h: number;
  nonce: number;
}
export interface SyncSource {
  path: string;
  line: number;
  column: number;
}

export type BibStatus = "verified" | "mismatch" | "not_found" | "unchecked";
export interface BibCheck {
  key: string;
  file: string;
  line: number | null;
  status: BibStatus;
  detail: string;
  found_title: string;
  found_id: string;
  source: string;
}
export interface BibVerifyReport {
  results: BibCheck[];
  counts: Record<BibStatus, number>;
  total: number;
  checked: number;
  truncated: boolean;
}

export interface ZoteroLibrary {
  id: string;
  name: string;
}
export interface ZoteroCollection {
  key: string;
  name: string;
  parent: string | null;
  items: number;
}
export interface ZoteroResult {
  file: string;
  total_in_zotero: number;
  added: number;
  skipped_existing: number;
  renamed: [string, string][];
  truncated: boolean;
}
