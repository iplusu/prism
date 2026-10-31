// Typed client for the Prism REST API. Same-origin cookie auth; every
// state-changing request carries the CSRF header the backend requires.
import type {
  AgentPolicy,
  AuthConfig,
  BibVerifyReport,
  Build,
  ChatMessage,
  CollabToken,
  FileContent,
  FileEntry,
  LlmTestReport,
  Outline,
  PaperHit,
  PdfMark,
  Project,
  ProjectStatus,
  Proposal,
  ProposalDetail,
  ReferenceImport,
  ReviewReport,
  Role,
  SearchResult,
  SyncSource,
  SystemStatus,
  Task,
  TaskContextSpec,
  TaskDetail,
  TaskMode,
  TemplateInfo,
  ToolHealth,
  User,
  Version,
  ZoteroCollection,
  ZoteroLibrary,
  ZoteroResult,
  VersionDiff,
} from "./types";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(ApiError.describe(status, detail));
    this.status = status;
    this.detail = detail;
  }
  static describe(status: number, detail: unknown): string {
    if (typeof detail === "string" && detail) return detail;
    if (detail && typeof detail === "object") {
      const d = detail as Record<string, unknown>;
      if (typeof d.message === "string") return d.message;
      if (Array.isArray(detail)) {
        // FastAPI validation errors: [{loc, msg, type}]
        return detail
          .map((e) => {
            const x = e as { loc?: unknown[]; msg?: string };
            const loc = Array.isArray(x.loc) ? x.loc.filter((p) => p !== "body").join(".") : "";
            return loc ? `${loc}: ${x.msg ?? ""}` : String(x.msg ?? "");
          })
          .join("; ");
      }
    }
    return `request failed (HTTP ${status})`;
  }
}

/** Detail of a 409 on PUT /files/content. */
export interface SaveConflictDetail {
  message: string;
  conflicts: unknown[];
}

type UnauthorizedListener = () => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();
export function onUnauthorized(fn: UnauthorizedListener): () => void {
  unauthorizedListeners.add(fn);
  return () => unauthorizedListeners.delete(fn);
}

interface RequestOpts {
  method?: string;
  body?: unknown;
  form?: FormData;
  /** Do not trigger the global "signed out" handler on 401 (login/me). */
  quiet401?: boolean;
  signal?: AbortSignal;
}

async function request<T>(url: string, opts: RequestOpts = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET" && method !== "HEAD") headers["X-Prism-CSRF"] = "1";
  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(url, { method, headers, body, credentials: "same-origin", signal: opts.signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "cannot reach the Prism server (network error)");
  }
  if (res.status === 401 && !opts.quiet401) unauthorizedListeners.forEach((fn) => fn());
  const ct = res.headers.get("content-type") || "";
  const data: unknown = ct.includes("application/json") ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : data;
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

const enc = encodeURIComponent;
const P = (pid: string) => `/api/projects/${enc(pid)}`;

export const api = {
  // --- auth -------------------------------------------------------------
  authConfig: () => request<AuthConfig>("/api/auth/config", { quiet401: true }),
  me: () => request<User>("/api/auth/me", { quiet401: true }),
  login: (username: string, password: string) =>
    request<User>("/api/auth/login", { method: "POST", body: { username, password }, quiet401: true }),
  signup: (username: string, password: string, display_name: string) =>
    request<User>("/api/auth/signup", { method: "POST", body: { username, password, display_name }, quiet401: true }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST", quiet401: true }),
  users: () => request<User[]>("/api/users"),
  createUser: (body: { username: string; password: string; display_name: string; is_admin: boolean }) =>
    request<User>("/api/users", { method: "POST", body }),

  // --- projects ---------------------------------------------------------
  projects: (q = "") => request<Project[]>(`/api/projects${q ? `?q=${enc(q)}` : ""}`),
  templates: () => request<TemplateInfo[]>("/api/projects/templates"),
  createProject: (name: string, template: string) =>
    request<Project>("/api/projects", { method: "POST", body: { name, template } }),
  importProject: (file: File, name: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", name);
    return request<Project>("/api/projects/import", { method: "POST", form });
  },
  project: (pid: string) => request<Project>(P(pid)),
  patchProject: (pid: string, body: Partial<Pick<Project, "name" | "description" | "main_file" | "engine">>) =>
    request<Project>(P(pid), { method: "PATCH", body }),
  putPolicy: (pid: string, body: AgentPolicy) => request<Project>(`${P(pid)}/policy`, { method: "PUT", body }),
  deleteProject: (pid: string) => request<{ ok: boolean }>(P(pid), { method: "DELETE" }),
  addMember: (pid: string, username: string, role: Role) =>
    request<Project>(`${P(pid)}/members`, { method: "POST", body: { username, role } }),
  removeMember: (pid: string, uid: string) =>
    request<Project>(`${P(pid)}/members/${enc(uid)}`, { method: "DELETE" }),
  status: (pid: string) => request<ProjectStatus>(`${P(pid)}/status`),
  collabToken: (pid: string) => request<CollabToken>(`${P(pid)}/collab-token`),
  exportZipUrl: (pid: string) => `${P(pid)}/export.zip`,
  exportArxivUrl: (pid: string) => `${P(pid)}/export-arxiv.zip`,
  arxivCheck: (pid: string) => request<ReviewReport>(`${P(pid)}/arxiv-check`),
  exportPdfUrl: (pid: string) => `${P(pid)}/export.pdf`,

  // --- files ------------------------------------------------------------
  files: (pid: string) => request<FileEntry[]>(`${P(pid)}/files`),
  fileContent: (pid: string, path: string) => request<FileContent>(`${P(pid)}/files/content?path=${enc(path)}`),
  rawUrl: (pid: string, path: string) => `${P(pid)}/files/raw?path=${enc(path)}`,
  saveFile: (pid: string, path: string, text: string, base_sha: string | null) =>
    request<{ path: string; sha: string }>(`${P(pid)}/files/content`, { method: "PUT", body: { path, text, base_sha } }),
  createFile: (pid: string, path: string, text = "") =>
    request<{ path: string; sha: string }>(`${P(pid)}/files`, { method: "POST", body: { path, text } }),
  uploadFile: (pid: string, file: File, directory: string, overwrite = true) => {
    const form = new FormData();
    form.append("file", file);
    form.append("directory", directory);
    form.append("overwrite", String(overwrite));
    return request<FileEntry>(`${P(pid)}/files/upload`, { method: "POST", form });
  },
  renameFile: (pid: string, src: string, dst: string) =>
    request<{ path: string }>(`${P(pid)}/files/rename`, { method: "POST", body: { src, dst } }),
  deleteFile: (pid: string, path: string) =>
    request<{ ok: boolean }>(`${P(pid)}/files?path=${enc(path)}`, { method: "DELETE" }),
  outline: (pid: string) => request<Outline>(`${P(pid)}/outline`),
  search: (pid: string, q: string, regex: boolean, caseSensitive: boolean) =>
    request<{ results: SearchResult[]; truncated: boolean }>(
      `${P(pid)}/search?q=${enc(q)}&regex=${regex}&case=${caseSensitive}`,
    ),

  review: (pid: string) => request<ReviewReport>(`${P(pid)}/review`),
  completeModels: () => request<{ default: string; models: string[] }>("/api/complete/models"),
  complete: (pid: string, path: string, prefix: string, suffix: string, model: string | null, signal?: AbortSignal) =>
    request<{ completion: string; model: string; latency_seconds?: number }>(`${P(pid)}/complete`, {
      method: "POST",
      body: { path, prefix, suffix, model },
      signal,
    }),
  synctexForward: (bid: string, file: string, line: number) =>
    request<{ build_id: string; boxes: Omit<PdfMark, "nonce">[] }>(`/api/builds/${enc(bid)}/synctex/forward?file=${enc(file)}&line=${line}`),
  synctexInverse: (bid: string, page: number, x: number, y: number) =>
    request<{ build_id: string; source: SyncSource | null }>(`/api/builds/${enc(bid)}/synctex/inverse?page=${page}&x=${x.toFixed(2)}&y=${y.toFixed(2)}`),
  verifyReferences: (pid: string) => request<BibVerifyReport>(`${P(pid)}/references/verify`, { method: "POST" }),
  zoteroLibraries: (apiKey: string) => request<ZoteroLibrary[]>("/api/zotero/libraries", { method: "POST", body: { api_key: apiKey } }),
  zoteroCollections: (library: string, apiKey: string | null) =>
    request<ZoteroCollection[]>("/api/zotero/collections", { method: "POST", body: { library, api_key: apiKey } }),
  zoteroImport: (pid: string, library: string, apiKey: string | null, collection: string | null) =>
    request<ZoteroResult>(`${P(pid)}/zotero/import`, { method: "POST", body: { library, api_key: apiKey, collection } }),
  searchPapers: (q: string) => request<PaperHit[]>(`/api/references/search?q=${enc(q)}`),
  importReference: (pid: string, source: "arxiv" | "doi", id: string) =>
    request<ReferenceImport>(`${P(pid)}/references`, { method: "POST", body: { source, id } }),

  // --- builds -----------------------------------------------------------
  compile: (pid: string, body?: { main_file?: string; fallback_to_main?: boolean }) =>
    request<Build & { fell_back_from: string | null }>(`${P(pid)}/builds`, { method: "POST", body: body ?? undefined }),
  builds: (pid: string, limit = 20) => request<Build[]>(`${P(pid)}/builds?limit=${limit}`),
  build: (bid: string) => request<Build>(`/api/builds/${enc(bid)}`),
  buildPdfUrl: (bid: string) => `/api/builds/${enc(bid)}/pdf`,

  // --- tasks ------------------------------------------------------------
  createTask: (pid: string, request_: string, mode: TaskMode, context: TaskContextSpec) =>
    request<Task>(`${P(pid)}/tasks`, { method: "POST", body: { request: request_, mode, context } }),
  installTexPackage: (pid: string, file: string) =>
    request<{ file: string; package: string | null; installed: boolean; note: string }>(`${P(pid)}/tex-packages`, { method: "POST", body: { file } }),
  tasks: (pid: string, limit = 50) => request<Task[]>(`${P(pid)}/tasks?limit=${limit}`),
  task: (tid: string) => request<TaskDetail>(`/api/tasks/${enc(tid)}`),
  taskStreamUrl: (tid: string, after = 0) => `/api/tasks/${enc(tid)}/stream${after ? `?after=${after}` : ""}`,
  cancelTask: (tid: string) => request<Task>(`/api/tasks/${enc(tid)}/cancel`, { method: "POST" }),
  retryTask: (tid: string) => request<Task>(`/api/tasks/${enc(tid)}/retry`, { method: "POST" }),
  messages: (pid: string) => request<ChatMessage[]>(`${P(pid)}/messages`),

  // --- proposals --------------------------------------------------------
  proposals: (pid: string, which: "open" | "all") => request<Proposal[]>(`${P(pid)}/proposals?status=${which}`),
  proposal: (id: string) => request<ProposalDetail>(`/api/proposals/${enc(id)}`),
  proposalPdfUrl: (id: string) => `/api/proposals/${enc(id)}/pdf`,
  applyProposal: (id: string) => request<Proposal>(`/api/proposals/${enc(id)}/apply`, { method: "POST" }),
  rejectProposal: (id: string) => request<Proposal>(`/api/proposals/${enc(id)}/reject`, { method: "POST" }),
  selectHunks: (id: string, selections: Record<string, number[]>) =>
    request<Proposal>(`/api/proposals/${enc(id)}/select`, { method: "POST", body: { selections } }),
  resolveConflicts: (id: string, resolutions: Record<string, string>, choices: Record<string, "current" | "proposal"> = {}) =>
    request<Proposal>(`/api/proposals/${enc(id)}/resolve`, { method: "POST", body: { resolutions, choices } }),

  // --- versions ---------------------------------------------------------
  versions: (pid: string) => request<Version[]>(`${P(pid)}/versions`),
  saveVersion: (pid: string, label: string) =>
    request<{ revision: string; label: string }>(`${P(pid)}/versions`, { method: "POST", body: { label } }),
  versionDiff: (pid: string, rev: string) => request<VersionDiff>(`${P(pid)}/versions/${enc(rev)}/diff`),
  restoreVersion: (pid: string, rev: string) =>
    request<Proposal>(`${P(pid)}/versions/${enc(rev)}/restore`, { method: "POST" }),

  // --- system -----------------------------------------------------------
  system: () => request<SystemStatus>("/api/system/status"),
  testLlm: () => request<LlmTestReport>("/api/system/llm/test", { method: "POST" }),
  refreshTools: () => request<ToolHealth[]>("/api/system/tools/refresh", { method: "POST" }),
};

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}
