import type { ReactNode } from "react";
import type { FileEntry } from "../types";
import CheckPanel from "./CheckPanel";
import FileTree from "./FileTree";
import InfoPanel from "./InfoPanel";
import HistoryPanel from "./HistoryPanel";
import { IconBook, IconFile, IconHistory, IconInfo, IconList, IconReview, IconSearch } from "./Icons";
import OutlinePanel from "./OutlinePanel";
import ReferencesPanel from "./ReferencesPanel";
import SearchPanel from "./SearchPanel";

export type SideTab = "files" | "outline" | "search" | "refs" | "check" | "history" | "info";

interface Props {
  pid: string;
  tab: SideTab;
  onTab: (t: SideTab) => void;
  files: FileEntry[] | undefined;
  filesError: string | null;
  activePath: string | null;
  mainFile: string;
  canEdit: boolean;
  outlineKey: unknown;
  onOpen: (path: string) => void;
  onJump: (path: string, line: number) => void;
  onFilesChanged: () => void;
  onRenamed: (src: string, dst: string) => void;
  onDeleted: (path: string) => void;
}

interface TabDef { id: SideTab; label: string; icon: ReactNode }
const T: Record<SideTab, TabDef> = {
  files: { id: "files", label: "Files", icon: <IconFile /> },
  outline: { id: "outline", label: "Outline", icon: <IconList /> },
  search: { id: "search", label: "Search", icon: <IconSearch /> },
  refs: { id: "refs", label: "Refs", icon: <IconBook /> },
  check: { id: "check", label: "Check", icon: <IconReview /> },
  history: { id: "history", label: "History", icon: <IconHistory /> },
  info: { id: "info", label: "Info", icon: <IconInfo /> },
};

// The top row has one tab per group; a group with several panels switches between them with the small buttons under it.
const GROUPS: { label: string; icon: ReactNode; tabs: SideTab[] }[] = [
  { label: "Files", icon: <IconFile />, tabs: ["files", "outline", "search"] },
  { label: "Refs", icon: <IconBook />, tabs: ["refs"] },
  { label: "Check", icon: <IconReview />, tabs: ["check"] },
  { label: "Project", icon: <IconInfo />, tabs: ["info", "history"] },
];

export default function Sidebar(p: Props) {
  const group = GROUPS.find((g) => g.tabs.includes(p.tab));
  return (
    <div className="sidebar">
      <div className="side-tabs" role="tablist" aria-label="Sidebar" aria-orientation="horizontal">
        {GROUPS.map((g) => {
          const active = g.tabs.includes(p.tab);
          return (
            <button
              key={g.label}
              role="tab"
              aria-selected={active}
              className={`side-tab ${active ? "active" : ""}`}
              onClick={() => !active && p.onTab(g.tabs[0])}
              aria-label={g.label}
              title={g.label}
            >
              {g.icon}
              <span className="side-tab-label">{g.label}</span>
            </button>
          );
        })}
      </div>
      {group && group.tabs.length > 1 && (
        <div className="side-subtabs" role="tablist" aria-label={group.label}>
          {group.tabs.map((id) => (
            <button key={id} role="tab" aria-selected={p.tab === id} className={`side-subtab ${p.tab === id ? "active" : ""}`} onClick={() => p.onTab(id)}>
              {T[id].label}
            </button>
          ))}
        </div>
      )}
      <div className="sidebar-body" role="tabpanel">
        {p.tab === "files" && (
          <FileTree
            pid={p.pid}
            files={p.files}
            error={p.filesError}
            activePath={p.activePath}
            mainFile={p.mainFile}
            canEdit={p.canEdit}
            onOpen={p.onOpen}
            onChanged={p.onFilesChanged}
            onRenamed={p.onRenamed}
            onDeleted={p.onDeleted}
          />
        )}
        {p.tab === "outline" && <OutlinePanel pid={p.pid} refreshKey={p.outlineKey} onJump={p.onJump} />}
        {p.tab === "search" && <SearchPanel pid={p.pid} onJump={p.onJump} />}
        {p.tab === "refs" && <ReferencesPanel pid={p.pid} canEdit={p.canEdit} onImported={p.onFilesChanged} onJump={p.onJump} />}
        {p.tab === "check" && <CheckPanel pid={p.pid} onJump={p.onJump} />}
        {p.tab === "info" && <InfoPanel pid={p.pid} refreshKey={p.outlineKey} />}
        {p.tab === "history" && <HistoryPanel pid={p.pid} canEdit={p.canEdit} />}
      </div>
    </div>
  );
}
