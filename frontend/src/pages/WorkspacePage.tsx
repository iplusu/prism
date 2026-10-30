import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle, type ImperativePanelHandle } from "react-resizable-panels";
import { useParams } from "react-router-dom";
import { api, errorMessage } from "../api";
import { useApp } from "../appContext";
import { useCollab, useCollabStatus } from "../collab";
import AiPanel from "../components/AiPanel";
import BottomPanel, { type BottomTab } from "../components/BottomPanel";
import { flushAll, type DocCache, type JumpTarget, type SaveState, type SelectionInfo } from "../components/CodeEditor";
import EditorArea from "../components/EditorArea";
import PdfPanel from "../components/PdfPanel";
import Sidebar, { type SideTab } from "../components/Sidebar";
import { ErrorBox, Loading, Tabs } from "../components/ui";
import WorkspaceHeader, { saveIndicatorFor, type PanelToggles } from "../components/WorkspaceHeader";
import type { CompletionData } from "../editor/setup";
import type { Build, PdfMark, ProjectStatus } from "../types";
import { useAsync, useInterval, useMediaQuery } from "../util";

type NarrowTab = "files" | "editor" | "pdf" | "ai";

const AUTO_COMPILE_IDLE_MS = 2500;

function loadTabs(pid: string): { tabs: string[]; active: string | null } | null {
  try {
    const raw = localStorage.getItem(`prism-tabs-${pid}`);
    return raw ? (JSON.parse(raw) as { tabs: string[]; active: string | null }) : null;
  } catch {
    return null;
  }
}

export default function WorkspacePage() {
  const { pid = "" } = useParams();
  const { user, config } = useApp();
  const narrow = useMediaQuery("(max-width: 899px)");

  const project = useAsync(() => api.project(pid), [pid]);
  const files = useAsync(() => api.files(pid), [pid]);
  const collab = useCollab(pid, user);
  const { status: collabStatus, peers } = useCollabStatus(collab.session);

  const role = project.data?.role ?? null;
  const canEdit = role === "owner" || role === "editor";

  // ---------------------------------------------------------- status poll ---
  const [status, setStatus] = useState<ProjectStatus | undefined>(undefined);
  const [statusError, setStatusError] = useState<string | null>(null);
  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await api.status(pid));
      setStatusError(null);
    } catch (e) {
      setStatusError(errorMessage(e));
    }
  }, [pid]);
  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);
  const busy =
    !!status &&
    (status.active_tasks.length > 0 || status.last_build?.status === "queued" || status.last_build?.status === "running");
  useInterval(() => {
    if (document.visibilityState === "visible") void refreshStatus();
  }, busy ? 1500 : 4000);

  // Latest build with log + diagnostics (refetched when it changes state).
  const [build, setBuild] = useState<Build | undefined>(undefined);
  const [buildError, setBuildError] = useState<string | null>(null);
  const lb = status?.last_build;
  useEffect(() => {
    if (!lb) {
      setBuild(undefined);
      return;
    }
    let cancelled = false;
    api.build(lb.id).then(
      (b) => !cancelled && (setBuild(b), setBuildError(null)),
      (e) => !cancelled && setBuildError(errorMessage(e)),
    );
    return () => {
      cancelled = true;
    };
  }, [lb?.id, lb?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  // -------------------------------------------------------------- files ---
  useEffect(() => {
    if (!collab.session) return;
    let t: number | undefined;
    const off = collab.session.onFilesChanged(() => {
      window.clearTimeout(t);
      t = window.setTimeout(() => void files.reload(), 400);
    });
    return () => {
      off();
      window.clearTimeout(t);
    };
  }, [collab.session]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------- open tabs ---
  const [tabs, setTabs] = useState<string[]>(() => loadTabs(pid)?.tabs ?? []);
  const [active, setActive] = useState<string | null>(() => loadTabs(pid)?.active ?? null);
  useEffect(() => {
    try {
      localStorage.setItem(`prism-tabs-${pid}`, JSON.stringify({ tabs, active }));
    } catch {
      /* ignore */
    }
  }, [pid, tabs, active]);
  // Open the main file when nothing is open yet.
  useEffect(() => {
    if (!active && project.data && files.data?.some((f) => f.path === project.data!.main_file)) {
      setTabs((t) => (t.includes(project.data!.main_file) ? t : [...t, project.data!.main_file]));
      setActive(project.data.main_file);
    }
  }, [project.data, files.data, active]);

  const [narrowTab, setNarrowTab] = useState<NarrowTab>("editor");
  const openFile = useCallback(
    (path: string) => {
      setTabs((t) => (t.includes(path) ? t : [...t, path]));
      setActive(path);
      if (narrow) setNarrowTab("editor");
    },
    [narrow],
  );
  const closeTab = (path: string) => {
    setTabs((t) => {
      const i = t.indexOf(path);
      const next = t.filter((x) => x !== path);
      if (active === path) setActive(next[Math.min(i, next.length - 1)] ?? null);
      return next;
    });
  };
  const [jump, setJump] = useState<JumpTarget | null>(null);
  const jumpTo = useCallback(
    (path: string, line: number) => {
      const clean = path.replace(/^\.\//, "");
      openFile(clean);
      setJump({ path: clean, line, nonce: Date.now() });
    },
    [openFile],
  );

  // ------------------------------------------------------ editor state ---
  const cache = useRef<DocCache>(new Map()).current;
  const [saveStates, setSaveStates] = useState<Record<string, SaveState>>({});
  const onSaveState = useCallback((path: string, s: SaveState) => setSaveStates((m) => ({ ...m, [path]: s })), []);
  const onSaved = useCallback(() => void refreshStatus(), [refreshStatus]);
  const [selection, setSelection] = useState<SelectionInfo | null>(null);
  // "Ask AI" on a selection: the snapshot travels with a nonce so the AI panel can pin it, switch to
  // the selection context and focus its request box (the editor may unmount on narrow screens).
  const [ask, setAsk] = useState<{ sel: SelectionInfo; nonce: number } | null>(null);

  // Warn before leaving with unsaved REST edits.
  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => {
      if ([...cache.values()].some((c) => c.dirty)) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [cache]);

  // Completion data for \ref{} and \cite{} (refreshed as the manuscript changes, at most every 15 s).
  const [aiOn, setAiOn] = useState(() => {
    try {
      return localStorage.getItem("prism-ai-complete") === "1";
    } catch {
      return false;
    }
  });
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiModel, setAiModelState] = useState<string | null>(() => {
    try {
      return localStorage.getItem("prism-ai-complete-model") || null;
    } catch {
      return null;
    }
  });
  const [aiModels, setAiModels] = useState<{ default: string; models: string[] } | null>(null);
  useEffect(() => {
    if (!aiOn || aiModels) return;
    api.completeModels().then(setAiModels, (e) => setAiError(errorMessage(e)));
  }, [aiOn, aiModels]);
  const setAiModel = useCallback((m: string | null) => {
    setAiModelState(m);
    setAiError(null);
    try {
      if (m) localStorage.setItem("prism-ai-complete-model", m);
      else localStorage.removeItem("prism-ai-complete-model");
    } catch {
      /* preference just won't persist */
    }
  }, []);
  useEffect(() => {
    // a remembered model the server no longer serves falls back to the default
    if (aiModels && aiModel && !aiModels.models.includes(aiModel)) setAiModel(null);
  }, [aiModels, aiModel]);
  const completion = useRef<CompletionData>({
    labels: [],
    bibKeys: [],
    ai: { pid, on: aiOn, model: aiModel, report: setAiError },
    cursor: null,
    sync: () => undefined,
  });
  completion.current.ai.pid = pid;
  completion.current.ai.on = aiOn;
  completion.current.ai.model = aiModel;
  const toggleAi = useCallback(() => {
    setAiOn((v) => {
      try {
        localStorage.setItem("prism-ai-complete", v ? "0" : "1");
      } catch {
        /* preference just won't persist */
      }
      return !v;
    });
    setAiError(null);
  }, []);
  const lastOutline = useRef(0);
  useEffect(() => {
    if (!status?.current_hash || Date.now() - lastOutline.current < 15000) return;
    lastOutline.current = Date.now();
    api.outline(pid).then(
      (o) => {
        completion.current.labels = o.labels.map((l) => l.label);
        completion.current.bibKeys = o.bib.map((b) => ({ key: b.key, title: b.title }));
      },
      () => undefined,
    );
  }, [pid, status?.current_hash]);

  // -------------------------------------------------------------- compile ---
  const [compiling, setCompiling] = useState(false);
  const [compileError, setCompileError] = useState<string | null>(null);
  // Which file Compile builds: the open .tex file (its PDF is named after it) or the project's main file.
  // A file without \documentclass (an \input section) falls back to the main file on the server.
  const [compileCurrent, setCompileCurrent] = useState(() => {
    try {
      return localStorage.getItem("prism-compile-current") !== "0";
    } catch {
      return true;
    }
  });
  const toggleCompileCurrent = useCallback(() => {
    setCompileCurrent((v) => {
      try {
        localStorage.setItem("prism-compile-current", v ? "0" : "1");
      } catch {
        /* preference just won't persist */
      }
      return !v;
    });
  }, []);
  const mainFile = project.data?.main_file;
  const target = compileCurrent && active && /\.tex$/i.test(active) ? active : mainFile;
  const compile = useCallback(async () => {
    if (!canEdit || compiling) return;
    setCompiling(true);
    setCompileError(null);
    try {
      if (!collab.enabled) {
        const unsaved = await flushAll(pid, cache, onSaveState, () => undefined);
        if (unsaved.length) {
          setCompileError(`Not compiled: unsaved changes in ${unsaved.join(", ")}`);
          return;
        }
      }
      const r = await api.compile(pid, target && target !== mainFile ? { main_file: target, fallback_to_main: true } : undefined);
      if (r.fell_back_from) setCompileError(`${r.fell_back_from} has no \\documentclass, so ${r.main_file} was compiled`);
      await refreshStatus();
    } catch (e) {
      setCompileError(errorMessage(e));
    } finally {
      setCompiling(false);
    }
  }, [canEdit, compiling, collab.enabled, pid, cache, onSaveState, refreshStatus, target, mainFile]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void compile();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [compile]);
  // ------------------------------------------------------- SyncTeX ---
  const [pdfMark, setPdfMark] = useState<PdfMark | null>(null);
  const [syncNote, setSyncNote] = useState<string | null>(null);
  const noteTimer = useRef<number | undefined>(undefined);
  const note = useCallback((m: string) => {
    setSyncNote(m);
    window.clearTimeout(noteTimer.current);
    noteTimer.current = window.setTimeout(() => setSyncNote(null), 6000);
  }, []);
  const okBuild = status?.last_success_build ?? null;
  const showInPdf = useCallback(
    async (path: string, line: number) => {
      if (!okBuild?.has_pdf) return note("No PDF yet: compile first.");
      try {
        const r = await api.synctexForward(okBuild.id, path, line);
        const b = r.boxes[0];
        if (!b) return note(`Line ${line} of ${path} has no position in the PDF (comment, preamble or not part of this build).`);
        setPdfMark({ ...b, nonce: Date.now() });
        if (narrow) setNarrowTab("pdf");
        else pdfRef.current?.expand();
        if (status?.pdf_stale) note("The PDF is older than the text; the position may be off. Compile to refresh.");
      } catch (e) {
        note(errorMessage(e));
      }
    },
    [okBuild, narrow, status?.pdf_stale, note],
  );
  completion.current.sync = (path, line) => void showInPdf(path, line);
  const sourceAt = useCallback(
    async (page: number, x: number, y: number) => {
      if (!okBuild?.has_pdf) return;
      try {
        const r = await api.synctexInverse(okBuild.id, page, x, y);
        if (!r.source) return note("That spot does not come from a project file.");
        jumpTo(r.source.path, r.source.line);
        if (status?.pdf_stale) note("The PDF is older than the text; the line may be off. Compile to refresh.");
      } catch (e) {
        note(errorMessage(e));
      }
    },
    [okBuild, jumpTo, status?.pdf_stale, note],
  );

  const building = compiling || lb?.status === "queued" || lb?.status === "running";

  // Auto compile (off by default; compiling costs server CPU). Once the text has been unchanged for
  // AUTO_COMPILE_IDLE_MS and no build exists for exactly this text, compile it. Each text is tried at most
  // once, so a failing build is not retried in a loop; the next edit tries again.
  const [autoCompile, setAutoCompile] = useState(() => {
    try {
      return localStorage.getItem("prism-auto-compile") === "1";
    } catch {
      return false;
    }
  });
  const toggleAutoCompile = useCallback(() => {
    setAutoCompile((v) => {
      try {
        localStorage.setItem("prism-auto-compile", v ? "0" : "1");
      } catch {
        /* preference just won't persist */
      }
      return !v;
    });
  }, []);
  const autoTried = useRef<string | null>(null);
  const curHash = status?.current_hash;
  const builtHash = lb?.source_hash;
  useEffect(() => {
    const key = `${curHash}|${target}`;
    if (!autoCompile || !canEdit || !curHash || building || (builtHash === curHash && lb?.main_file === target) || autoTried.current === key) return;
    const t = window.setTimeout(() => {
      autoTried.current = key;
      void compile();
    }, AUTO_COMPILE_IDLE_MS);
    return () => window.clearTimeout(t);
  }, [autoCompile, canEdit, curHash, building, builtHash, compile, target, lb?.main_file]);

  // --------------------------------------------------------------- panels ---
  const sideRef = useRef<ImperativePanelHandle>(null);
  const bottomRef = useRef<ImperativePanelHandle>(null);
  const pdfRef = useRef<ImperativePanelHandle>(null);
  const aiRef = useRef<ImperativePanelHandle>(null);
  const refs = { side: sideRef, bottom: bottomRef, pdf: pdfRef, ai: aiRef };
  const [panels, setPanels] = useState<PanelToggles>({ side: true, bottom: true, pdf: true, ai: true });
  const togglePanel = (k: keyof PanelToggles) => {
    const r = refs[k].current;
    if (!r) return;
    if (r.isCollapsed()) r.expand();
    else r.collapse();
  };
  const setPanel = (k: keyof PanelToggles, v: boolean) => setPanels((p) => (p[k] === v ? p : { ...p, [k]: v }));
  // Sync toggle buttons with layouts restored from localStorage.
  const projectLoaded = !!project.data;
  useEffect(() => {
    if (narrow || !projectLoaded) return;
    const t = window.setTimeout(() => {
      setPanels({
        side: !sideRef.current?.isCollapsed(),
        bottom: !bottomRef.current?.isCollapsed(),
        pdf: !pdfRef.current?.isCollapsed(),
        ai: !aiRef.current?.isCollapsed(),
      });
    }, 0);
    return () => window.clearTimeout(t);
  }, [narrow, projectLoaded]);
  const askAi = () => {
    if (!selection) return;
    setAsk((a) => ({ sel: selection, nonce: (a?.nonce ?? 0) + 1 }));
    if (narrow) setNarrowTab("ai");
    else if (aiRef.current?.isCollapsed()) aiRef.current.expand();
  };
  const [sideTab, setSideTab] = useState<SideTab>("files");
  const [bottomTab, setBottomTab] = useState<BottomTab>("log");
  const showLog = () => {
    setBottomTab(build?.diagnostics.length ? "problems" : "log");
    if (narrow) setNarrowTab("editor");
    else bottomRef.current?.expand();
  };

  // ------------------------------------------------------------- derived ---
  const activeTaskKey = status ? status.active_tasks.map((t) => `${t.id}:${t.status}`).join(",") : "";
  const refreshKey = `${activeTaskKey}|${status?.open_proposals ?? ""}`;
  const saveIndicator = saveIndicatorFor(
    { enabled: collab.enabled, status: collabStatus, readonly: !!collab.session?.readonly },
    !canEdit,
    active,
    saveStates,
  );
  const diagnostics = useMemo(() => build?.diagnostics ?? [], [build]);

  useEffect(() => {
    if (project.data) document.title = `${project.data.name} · Prism`;
  }, [project.data]);

  if (project.error && !project.data) {
    return (
      <div className="center-page">
        <div className="card narrow">
          <h1>Cannot open project</h1>
          <ErrorBox error={project.error} onRetry={project.reload} />
          <a className="btn" href="/">
            Back to projects
          </a>
        </div>
      </div>
    );
  }
  if (!project.data) return <Loading text="Opening project…" />;

  const sidebar = (
    <Sidebar
      pid={pid}
      tab={sideTab}
      onTab={setSideTab}
      files={files.data}
      filesError={files.error}
      activePath={active}
      mainFile={project.data.main_file}
      canEdit={canEdit}
      outlineKey={status?.current_hash}
      onOpen={openFile}
      onJump={jumpTo}
      onFilesChanged={() => {
        void files.reload();
        void refreshStatus();
      }}
      onRenamed={(src, dst) => {
        cache.delete(src);
        setTabs((t) => t.map((x) => (x === src ? dst : x)));
        if (active === src) setActive(dst);
        if (src === project.data?.main_file) void project.reload();
      }}
      onDeleted={(path) => {
        cache.delete(path);
        closeTab(path);
      }}
    />
  );
  const editor = (
    <EditorArea
      pid={pid}
      tabs={tabs}
      active={active}
      files={files.data}
      onActivate={setActive}
      onClose={closeTab}
      readOnly={!canEdit}
      collab={collab}
      cache={cache}
      saveStates={saveStates}
      jump={jump}
      diagnostics={diagnostics}
      completion={completion}
      ai={{ on: aiOn, error: aiError, toggle: toggleAi, model: aiModel, models: aiModels, setModel: setAiModel }}
      onSelection={setSelection}
      selection={selection}
      onAskAi={askAi}
      onSaveState={onSaveState}
      onSaved={onSaved}
    />
  );
  const bottom = (
    <BottomPanel
      pid={pid}
      tab={bottomTab}
      onTab={setBottomTab}
      status={status}
      build={build}
      buildError={buildError}
      onJump={jumpTo}
      tasksKey={refreshKey}
    />
  );
  const pdf = (
    <PdfPanel
      status={status}
      projectName={project.data.name}
      onShowLog={showLog}
      canCompile={canEdit}
      mark={pdfMark}
      onSourceClick={(page, x, y) => void sourceAt(page, x, y)}
      notice={syncNote}
    />
  );
  const ai = (
    <AiPanel
      pid={pid}
      canEdit={canEdit}
      demoMode={config.demo_mode}
      defaultMode={project.data.policy?.default_executor}
      selection={selection}
      ask={ask}
      activePath={active}
      refreshKey={refreshKey}
      onTaskChange={() => void refreshStatus()}
    />
  );

  return (
    <div className="workspace">
      <WorkspaceHeader
        project={project.data}
        status={status}
        canEdit={canEdit}
        saveIndicator={saveIndicator}
        compiling={building}
        compileError={compileError}
        onCompile={() => void compile()}
        compileTarget={target}
        mainFile={mainFile}
        compileCurrent={compileCurrent}
        onToggleCompileCurrent={toggleCompileCurrent}
        onExportError={setCompileError}
        autoCompile={autoCompile}
        onToggleAutoCompile={toggleAutoCompile}
        members={project.data.members ?? []}
        peers={peers}
        collabStatus={collabStatus}
        panels={narrow ? null : panels}
        onTogglePanel={togglePanel}
      />
      {statusError && <ErrorBox error={`Status update failed: ${statusError}`} onRetry={refreshStatus} />}
      {collab.error && <ErrorBox error={`Collaboration unavailable (${collab.error}); editing with autosave instead.`} />}
      {narrow ? (
        <div className="narrow-layout">
          <Tabs
            label="Workspace view"
            className="narrow-tabs"
            value={narrowTab}
            onChange={setNarrowTab}
            tabs={[
              { id: "files", label: "Files" },
              { id: "editor", label: "Editor" },
              { id: "pdf", label: "PDF" },
              { id: "ai", label: "AI" },
            ]}
          />
          <div className="narrow-body" role="tabpanel">
            {narrowTab === "files" && sidebar}
            {narrowTab === "editor" && (
              <PanelGroup direction="vertical" autoSaveId="prism-narrow-v">
                <Panel id="n-editor" order={1} minSize={30}>
                  {editor}
                </Panel>
                <PanelResizeHandle className="resize-handle h" />
                <Panel id="n-bottom" order={2} defaultSize={30} minSize={10} collapsible collapsedSize={4}>
                  {bottom}
                </Panel>
              </PanelGroup>
            )}
            {narrowTab === "pdf" && pdf}
            {narrowTab === "ai" && ai}
          </div>
        </div>
      ) : (
        <PanelGroup direction="horizontal" autoSaveId="prism-ws-h" className="ws-panels">
          <Panel
            id="side"
            order={1}
            ref={sideRef}
            defaultSize={17}
            minSize={10}
            collapsible
            collapsedSize={0}
            onCollapse={() => setPanel("side", false)}
            onExpand={() => setPanel("side", true)}
          >
            {sidebar}
          </Panel>
          <PanelResizeHandle className="resize-handle v" />
          <Panel id="center" order={2} minSize={25}>
            <PanelGroup direction="vertical" autoSaveId="prism-ws-v">
              <Panel id="editor" order={1} minSize={20}>
                {editor}
              </Panel>
              <PanelResizeHandle className="resize-handle h" />
              <Panel
                id="bottom"
                order={2}
                ref={bottomRef}
                defaultSize={24}
                minSize={8}
                collapsible
                collapsedSize={0}
                onCollapse={() => setPanel("bottom", false)}
                onExpand={() => setPanel("bottom", true)}
              >
                {bottom}
              </Panel>
            </PanelGroup>
          </Panel>
          <PanelResizeHandle className="resize-handle v" />
          <Panel
            id="pdf"
            order={3}
            ref={pdfRef}
            defaultSize={33}
            minSize={15}
            collapsible
            collapsedSize={0}
            onCollapse={() => setPanel("pdf", false)}
            onExpand={() => setPanel("pdf", true)}
          >
            {pdf}
          </Panel>
          <PanelResizeHandle className="resize-handle v" />
          <Panel
            id="ai"
            order={4}
            ref={aiRef}
            defaultSize={22}
            minSize={14}
            collapsible
            collapsedSize={0}
            onCollapse={() => setPanel("ai", false)}
            onExpand={() => setPanel("ai", true)}
          >
            {ai}
          </Panel>
        </PanelGroup>
      )}
    </div>
  );
}
