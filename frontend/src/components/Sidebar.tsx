import type { ReactNode } from "react";
import type { FileEntry } from "../types";
import CheckPanel from "./CheckPanel";
import FileTree from "./FileTree";
import HistoryPanel from "./HistoryPanel";
import { IconBook, IconFile, IconHistory, IconList, IconReview, IconSearch } from "./Icons";
import OutlinePanel from "./OutlinePanel";
import ReferencesPanel from "./ReferencesPanel";
import SearchPanel from "./SearchPanel";

export type SideTab = "files" | "outline" | "search" | "refs" | "check" | "history";

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

const TABS: { id: SideTab; label: string; icon: ReactNode }[] = [
  { id: "files", label: "Files", icon: <IconFile /> },
  { id: "outline", label: "Outline", icon: <IconList /> },
  { id: "search", label: "Search", icon: <IconSearch /> },
  { id: "refs", label: "Refs", icon: <IconBook /> },
  { id: "check", label: "Check", icon: <IconReview /> },
  { id: "history", label: "History", icon: <IconHistory /> },
];

export default function Sidebar(p: Props) {
  return (
    <div className="sidebar">
      <div className="side-tabs" role="tablist" aria-label="Sidebar" aria-orientation="horizontal">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={p.tab === t.id}
            className={`side-tab ${p.tab === t.id ? "active" : ""}`}
            onClick={() => p.onTab(t.id)}
            aria-label={t.label}
            title={t.label}
          >
            {t.icon}
            <span className="side-tab-label">{t.label}</span>
          </button>
        ))}
      </div>
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
        {p.tab === "history" && <HistoryPanel pid={p.pid} canEdit={p.canEdit} />}
      </div>
    </div>
  );
}
