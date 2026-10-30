import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { CollabStatus, Peer } from "../collab";
import type { Member, Project, ProjectStatus } from "../types";
import { basename } from "../util";
import { ThemeToggle, UserMenu } from "./AppHeader";
import type { SaveState } from "./CodeEditor";
import {
  IconBack,
  IconDownload,
  IconPanelBottom,
  IconPanelLeft,
  IconPdf,
  IconPlay,
  IconReview,
  IconSettings,
  IconSparkle,
  IconUsers,
} from "./Icons";
import { Badge, Menu, Spinner } from "./ui";

export interface PanelToggles {
  side: boolean;
  bottom: boolean;
  pdf: boolean;
  ai: boolean;
}

interface Props {
  project: Project | undefined;
  status: ProjectStatus | undefined;
  canEdit: boolean;
  saveIndicator: { tone: "ok" | "info" | "warn" | "bad" | "muted"; text: string; title?: string };
  compiling: boolean;
  compileError: string | null;
  onCompile: () => void;
  autoCompile: boolean;
  onToggleAutoCompile: () => void;
  members: Member[];
  peers: Peer[];
  collabStatus: CollabStatus | null;
  panels: PanelToggles | null; // null on narrow screens
  onTogglePanel: (k: keyof PanelToggles) => void;
}

export function saveIndicatorFor(
  collab: { enabled: boolean | undefined; status: CollabStatus | null; readonly: boolean },
  readOnly: boolean,
  active: string | null,
  states: Record<string, SaveState>,
): Props["saveIndicator"] {
  if (collab.enabled === undefined) return { tone: "muted", text: "…" };
  if (collab.enabled) {
    const ro = readOnly || collab.readonly ? " · read-only" : "";
    if (collab.status === "synced") return { tone: "ok", text: `Live · synced${ro}`, title: "Edits are shared with collaborators in real time" };
    if (collab.status === "offline")
      return { tone: "warn", text: `Offline${ro}`, title: "Not connected to the collaboration server. Local edits sync when the connection returns." };
    return { tone: "info", text: `Connecting…${ro}` };
  }
  if (readOnly) return { tone: "muted", text: "Read-only" };
  const others = Object.entries(states).filter(([p, s]) => p !== active && (s.kind === "error" || s.kind === "conflict"));
  const s = active ? states[active] : undefined;
  if (s?.kind === "conflict") return { tone: "bad", text: "Not saved — file changed elsewhere" };
  if (s?.kind === "error") return { tone: "bad", text: "Save failed", title: s.message };
  if (others.length) return { tone: "bad", text: `Not saved: ${others.map(([p]) => basename(p)).join(", ")}` };
  if (s?.kind === "saving") return { tone: "info", text: "Saving…" };
  if (s?.kind === "dirty") return { tone: "info", text: "Unsaved changes…" };
  if (s?.kind === "saved") return { tone: "ok", text: "All changes saved" };
  return { tone: "muted", text: "Autosave on" };
}

export default function WorkspaceHeader(p: Props) {
  const pid = p.project?.id ?? "";
  const open = p.status?.open_proposals ?? p.project?.open_proposals ?? 0;
  const hasPdf = !!(p.status?.last_success_build ?? p.project?.last_success_build);
  const toggles: { k: keyof PanelToggles; label: string; icon: ReactNode }[] = [
    { k: "side", label: "sidebar", icon: <IconPanelLeft /> },
    { k: "bottom", label: "output panel", icon: <IconPanelBottom /> },
    { k: "pdf", label: "PDF preview", icon: <IconPdf /> },
    { k: "ai", label: "AI assistant", icon: <IconSparkle /> },
  ];
  return (
    <header className="ws-header">
      <Link to="/" className="icon-btn" aria-label="Back to projects" title="All projects">
        <IconBack />
      </Link>
      <span className="brand-mark small-mark" aria-hidden="true" />
      <h1 className="ws-title ellipsis" title={p.project?.name}>
        {p.project?.name ?? "…"}
      </h1>
      {p.project?.role === "viewer" && <Badge status="viewer">read-only</Badge>}
      <span className={`save-ind tone-${p.saveIndicator.tone}`} title={p.saveIndicator.title ?? p.saveIndicator.text} role="status" aria-live="polite">
        <span className="dot" aria-hidden="true" />
        <span className="hide-narrow">{p.saveIndicator.text}</span>
      </span>
      <div className="spacer" />
      {p.panels && (
        <div className="panel-toggles" role="group" aria-label="Show or hide panels">
          {toggles.map((t) => (
            <button
              key={t.k}
              className={`icon-btn ${p.panels![t.k] ? "active" : ""}`}
              aria-pressed={p.panels![t.k]}
              aria-label={`${p.panels![t.k] ? "Hide" : "Show"} ${t.label}`}
              title={`${p.panels![t.k] ? "Hide" : "Show"} ${t.label}`}
              onClick={() => p.onTogglePanel(t.k)}
            >
              {t.icon}
            </button>
          ))}
        </div>
      )}
      {p.canEdit && (
        <button
          className="btn btn-primary"
          onClick={p.onCompile}
          disabled={p.compiling}
          title={p.compileError ?? "Compile the paper (Ctrl+S)"}
          aria-describedby={p.compileError ? "compile-err" : undefined}
        >
          {p.compiling ? <Spinner label="Compiling" /> : <IconPlay />} <span>Compile</span>
        </button>
      )}
      {p.canEdit && (
        <button
          className={`btn btn-sm btn-ghost auto-compile ${p.autoCompile ? "on" : ""}`}
          role="switch"
          aria-checked={p.autoCompile}
          onClick={p.onToggleAutoCompile}
          title="Auto compile: compile automatically a few seconds after you stop editing. Uses server CPU for every change."
        >
          Auto: {p.autoCompile ? "on" : "off"}
        </button>
      )}
      {p.compileError && (
        <span id="compile-err" className="text-bad small ellipsis compile-err" role="alert">
          {p.compileError}
        </span>
      )}
      <Link to={`/p/${pid}/review`} className="btn" aria-label={`Review changes${open ? ` (${open} open)` : ""}`}>
        <IconReview /> <span className="hide-narrow">Review</span>
        {open > 0 && <span className="count warn">{open}</span>}
      </Link>
      <Menu label="Export" buttonContent={<><IconDownload /> <span className="hide-narrow">Export</span></>} align="right">
        {(close) => (
          <div className="menu-list">
            <a className="menu-item" href={api.exportZipUrl(pid)} download onClick={close}>
              Source files (.zip)
            </a>
            {hasPdf ? (
              <a className="menu-item" href={api.exportPdfUrl(pid)} download onClick={close}>
                Last successful PDF
              </a>
            ) : (
              <span className="menu-item disabled muted">PDF — compile successfully first</span>
            )}
          </div>
        )}
      </Menu>
      <Menu
        label="Participants"
        align="right"
        buttonClass="btn btn-ghost"
        buttonContent={
          <>
            <IconUsers />
            {p.peers.length > 0 && <span className="count ok" aria-label={`${p.peers.length} online`}>{p.peers.length}</span>}
          </>
        }
      >
        {() => (
          <div className="menu-list participants">
            {p.collabStatus !== null && (
              <>
                <div className="menu-head">Editing now</div>
                {p.peers.length === 0 ? (
                  <div className="menu-info muted small">Nobody else is connected.</div>
                ) : (
                  p.peers.map((x) => (
                    <div key={x.clientId} className="menu-info">
                      <span className="peer-dot" style={{ background: x.color }} aria-hidden="true" />
                      {x.name}
                      {x.file && <span className="muted tiny"> · {basename(x.file)}</span>}
                    </div>
                  ))
                )}
                <div className="menu-sep" />
              </>
            )}
            <div className="menu-head">Members</div>
            {p.members.map((m) => (
              <div key={m.user.id} className="menu-info">
                <span>{m.user.display_name}</span> <span className="muted tiny">@{m.user.username}</span> <Badge status={m.role} />
              </div>
            ))}
            <div className="menu-sep" />
            <Link className="menu-item" to={`/p/${pid}/settings?tab=members`}>
              Manage members…
            </Link>
          </div>
        )}
      </Menu>
      <Link to={`/p/${pid}/settings?tab=project`} className="icon-btn" aria-label="Project settings" title="Settings">
        <IconSettings />
      </Link>
      <ThemeToggle />
      <UserMenu />
    </header>
  );
}

