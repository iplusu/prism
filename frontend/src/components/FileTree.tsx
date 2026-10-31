import { useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, api } from "../api";
import type { FileEntry } from "../types";
import { fmtBytes, useAction } from "../util";
import { IconChevron, IconEdit, IconFile, IconFolder, IconPlus, IconTrash, IconUpload } from "./Icons";
import { ConfirmButton, Empty, ErrorBox, Modal } from "./ui";

interface Dir {
  name: string;
  path: string;
  dirs: Map<string, Dir>;
  files: FileEntry[];
}

function buildTree(files: FileEntry[]): Dir {
  const root: Dir = { name: "", path: "", dirs: new Map(), files: [] };
  for (const f of files) {
    const parts = f.path.split("/");
    let d = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts.slice(0, i + 1).join("/");
      if (!d.dirs.has(parts[i])) d.dirs.set(parts[i], { name: parts[i], path: p, dirs: new Map(), files: [] });
      d = d.dirs.get(parts[i])!;
    }
    d.files.push(f);
  }
  return root;
}

interface Props {
  pid: string;
  files: FileEntry[] | undefined;
  error: string | null;
  activePath: string | null;
  mainFile: string;
  canEdit: boolean;
  onOpen: (path: string) => void;
  onChanged: () => void;
  onRenamed: (src: string, dst: string) => void;
  onDeleted: (path: string) => void;
}

type Dropped = { file: File; directory: string };

/** Files from a drop, including the contents of dropped folders (relative folder path kept). */
async function collectDropped(dt: DataTransfer): Promise<Dropped[]> {
  const out: Dropped[] = [];
  const walk = async (entry: FileSystemEntry, parent: string): Promise<void> => {
    if (entry.isFile) {
      const file = await new Promise<File>((res, rej) => (entry as FileSystemFileEntry).file(res, rej));
      out.push({ file, directory: parent });
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      const here = parent ? `${parent}/${entry.name}` : entry.name;
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((res, rej) => reader.readEntries(res, rej));
        if (!batch.length) break;
        for (const e of batch) await walk(e, here);
      }
    }
  };
  // entries must be read synchronously from the event, before any await
  const entries = Array.from(dt.items ?? [])
    .filter((i) => i.kind === "file")
    .map((i) => i.webkitGetAsEntry?.() ?? null);
  if (entries.length && entries.every((e) => e)) {
    for (const e of entries) await walk(e!, "");
  } else {
    for (const f of Array.from(dt.files)) out.push({ file: f, directory: "" });
  }
  return out;
}

export default function FileTree(p: Props) {
  const tree = useMemo(() => buildTree(p.files ?? []), [p.files]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [dir, setDir] = useState(""); // target folder for new files / uploads
  const [creating, setCreating] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameTo, setRenameTo] = useState("");
  const upload = useRef<HTMLInputElement>(null);

  const create = useAction(async (path: string) => {
    const r = await api.createFile(p.pid, path, "");
    p.onChanged();
    p.onOpen(r.path);
    setCreating(false);
    setNewPath("");
  });
  const rename = useAction(async (src: string, dst: string) => {
    const r = await api.renameFile(p.pid, src, dst);
    p.onRenamed(src, r.path);
    p.onChanged();
    setRenaming(null);
  });
  const del = useAction(async (path: string) => {
    await api.deleteFile(p.pid, path);
    p.onDeleted(path);
    p.onChanged();
  });
  // Uploads never replace a file silently: the server refuses a name that exists (409), the files that clash are
  // collected and the user decides once for all of them.
  const [clashes, setClashes] = useState<{ file: File; at: string; path: string }[]>([]);
  const send = useAction(async (items: Dropped[], target: string) => {
    const failed: string[] = [];
    const clash: { file: File; at: string; path: string }[] = [];
    for (const { file, directory } of items) {
      const at = [target, directory].filter(Boolean).join("/");
      try {
        await api.uploadFile(p.pid, file, at, false);
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) clash.push({ file, at, path: [at, file.name].filter(Boolean).join("/") });
        else failed.push(`${file.name}: ${e instanceof Error ? e.message : "failed"}`);
      }
    }
    p.onChanged();
    if (clash.length) setClashes(clash);
    if (failed.length) throw new Error(`${failed.length} of ${items.length} not uploaded — ${failed.join("; ")}`);
  });
  const replaceAll = useAction(async () => {
    const todo = clashes;
    setClashes([]);
    const failed: string[] = [];
    for (const c of todo) {
      try {
        await api.uploadFile(p.pid, c.file, c.at, true);
      } catch (e) {
        failed.push(`${c.file.name}: ${e instanceof Error ? e.message : "failed"}`);
      }
    }
    p.onChanged();
    if (failed.length) throw new Error(`${failed.length} of ${todo.length} not replaced — ${failed.join("; ")}`);
  });
  const up = useAction(async (files: FileList) => send.run(Array.from(files).map((file) => ({ file, directory: "" })), dir));
  const [dropOver, setDropOver] = useState<string | null>(null); // "" = whole panel, else folder path
  const drop = send;
  const dropProps = (target: string | null) => ({
    onDragOver: (e: React.DragEvent) => {
      if (!p.canEdit || !e.dataTransfer.types.includes("Files")) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = "copy";
      setDropOver(target ?? dir);
    },
    onDragLeave: (e: React.DragEvent) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDropOver(null);
    },
    onDrop: (e: React.DragEvent) => {
      if (!p.canEdit || !e.dataTransfer.types.includes("Files")) return;
      e.preventDefault();
      e.stopPropagation();
      setDropOver(null);
      const pending = collectDropped(e.dataTransfer);
      void pending.then((items) => (items.length ? drop.run(items, target ?? dir) : undefined));
    },
  });

  const toggle = (path: string) =>
    setCollapsed((s) => {
      const n = new Set(s);
      if (n.has(path)) n.delete(path);
      else n.add(path);
      return n;
    });

  function startCreate() {
    setCreating(true);
    setNewPath(dir ? `${dir}/` : "");
  }

  function submitCreate(e: FormEvent) {
    e.preventDefault();
    if (newPath.trim() && !newPath.endsWith("/")) void create.run(newPath.trim());
  }

  const renderDir = (d: Dir, depth: number): ReactNode => {
    const items: ReactNode[] = [];
    for (const sub of [...d.dirs.values()].sort((a, b) => a.name.localeCompare(b.name))) {
      const isOpen = !collapsed.has(sub.path);
      items.push(
        <li key={`d:${sub.path}`} role="treeitem" aria-expanded={isOpen} aria-selected={dir === sub.path}>
          <button
            {...dropProps(sub.path)}
            className={`tree-row ${dir === sub.path ? "selected-dir" : ""} ${dropOver === sub.path ? "drop-target" : ""}`}
            style={{ paddingLeft: 8 + depth * 14 }}
            onClick={() => {
              toggle(sub.path);
              setDir(sub.path);
            }}
            title={`${sub.path}/ — click to expand/collapse; new files and uploads go here`}
          >
            <IconChevron className={`chev ${isOpen ? "open" : ""}`} />
            <IconFolder />
            <span className="tree-name">{sub.name}</span>
          </button>
          {isOpen && <ul role="group">{renderDir(sub, depth + 1)}</ul>}
        </li>,
      );
    }
    for (const f of [...d.files].sort((a, b) => a.path.localeCompare(b.path))) {
      const name = f.path.split("/").pop()!;
      const isActive = f.path === p.activePath;
      items.push(
        <li key={f.path} role="treeitem" aria-selected={isActive} className="tree-file">
          {renaming === f.path ? (
            <form
              className="tree-rename"
              style={{ paddingLeft: 8 + depth * 14 }}
              onSubmit={(e) => {
                e.preventDefault();
                if (renameTo.trim() && renameTo !== f.path) void rename.run(f.path, renameTo.trim());
              }}
            >
              <label className="sr-only" htmlFor="rename-input">
                New path for {f.path}
              </label>
              <input
                id="rename-input"
                autoFocus
                value={renameTo}
                onChange={(e) => setRenameTo(e.target.value)}
                onKeyDown={(e) => e.key === "Escape" && setRenaming(null)}
              />
              <button className="btn btn-sm btn-primary" type="submit" disabled={rename.busy}>
                Rename
              </button>
              <button className="btn btn-sm" type="button" onClick={() => setRenaming(null)}>
                Cancel
              </button>
            </form>
          ) : (
            <div className={`tree-row-wrap ${isActive ? "active" : ""}`}>
              <button
                className="tree-row"
                style={{ paddingLeft: 22 + depth * 14 }}
                onClick={() => {
                  p.onOpen(f.path);
                  setDir(d.path);
                }}
                title={`${f.path} (${fmtBytes(f.size)}${f.is_text ? "" : ", binary"})`}
                aria-current={isActive ? "page" : undefined}
              >
                <IconFile />
                <span className="tree-name">{name}</span>
                {f.path === p.mainFile && <span className="badge badge-accent tiny">main</span>}
              </button>
              {p.canEdit && (
                <span className="tree-actions">
                  <button
                    className="icon-btn sm"
                    aria-label={`Rename ${f.path}`}
                    title="Rename / move"
                    onClick={() => {
                      setRenaming(f.path);
                      setRenameTo(f.path);
                    }}
                  >
                    <IconEdit />
                  </button>
                  <ConfirmButton
                    className="icon-btn sm"
                    ariaLabel={`Delete ${f.path}`}
                    question={`Delete ${name}?`}
                    confirmLabel="Delete"
                    onConfirm={() => del.run(f.path)}
                  >
                    <IconTrash />
                  </ConfirmButton>
                </span>
              )}
            </div>
          )}
        </li>,
      );
    }
    return items;
  };

  return (
    <div className={`side-panel ${dropOver !== null ? "drop-over" : ""}`} {...dropProps(null)}>
      <div className="side-head">
        <span className="side-title">Files</span>
        <div className="spacer" />
        {p.canEdit && (
          <>
            <button className="icon-btn sm" aria-label="New file" title={`New file${dir ? ` in ${dir}/` : ""}`} onClick={startCreate}>
              <IconPlus />
            </button>
            <button className="icon-btn sm" aria-label="Upload files" title={`Upload${dir ? ` into ${dir}/` : ""}`} onClick={() => upload.current?.click()}>
              <IconUpload />
            </button>
            <input
              ref={upload}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files?.length) void up.run(e.target.files);
                e.target.value = "";
              }}
            />
          </>
        )}
      </div>
      {creating && (
        <form className="side-form" onSubmit={submitCreate}>
          <label className="field">
            <span className="small">New file path (use “folder/name.tex” to create a folder)</span>
            <input autoFocus value={newPath} placeholder="sections/results.tex" onChange={(e) => setNewPath(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setCreating(false)} />
          </label>
          <div className="row-actions">
            <button className="btn btn-sm btn-primary" type="submit" disabled={create.busy || !newPath.trim() || newPath.endsWith("/")}>
              Create
            </button>
            <button className="btn btn-sm" type="button" onClick={() => setCreating(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {dir && p.canEdit && (
        <div className="muted small side-note">
          Target folder: <code>{dir}/</code>{" "}
          <button className="link-btn" onClick={() => setDir("")}>
            use root
          </button>
        </div>
      )}
      {p.canEdit && dropOver !== null && (
        <div className="drop-hint small" role="status">
          Drop to upload into <code>{dropOver ? `${dropOver}/` : "the project root"}</code>
        </div>
      )}
      {(up.busy || drop.busy || replaceAll.busy) && <div className="muted small side-note">Uploading…</div>}
      <ErrorBox error={p.error || create.error || rename.error || del.error || up.error || drop.error || replaceAll.error} />
      {clashes.length > 0 && (
        <Modal title={clashes.length === 1 ? "Replace existing file?" : `Replace ${clashes.length} existing files?`} onClose={() => setClashes([])}>
          <p className="small">
            {clashes.length === 1 ? "A file with this name is already in the project:" : "Files with these names are already in the project:"}
          </p>
          <ul className="small clash-list">
            {clashes.slice(0, 12).map((c) => (
              <li key={c.path}>
                <code>{c.path}</code>
                <span className="muted"> — {fmtBytes(p.files?.find((f) => f.path === c.path)?.size ?? 0)} now, {fmtBytes(c.file.size)} uploaded</span>
              </li>
            ))}
            {clashes.length > 12 && <li className="muted">… and {clashes.length - 12} more</li>}
          </ul>
          <p className="small muted">Each replaced file is first saved in the project history, so the old version can be restored. The other files of this upload were already added.</p>
          <div className="row-actions">
            <button className="btn btn-primary" onClick={() => void replaceAll.run()}>
              {clashes.length === 1 ? "Replace" : `Replace ${clashes.length} files`}
            </button>
            <button className="btn" onClick={() => setClashes([])}>
              Keep existing
            </button>
          </div>
        </Modal>
      )}
      <div className="side-scroll">
        {p.files && p.files.length === 0 ? (
          <Empty>No files.</Empty>
        ) : (
          <ul className="tree" role="tree" aria-label="Project files">
            {renderDir(tree, 0)}
          </ul>
        )}
      </div>
    </div>
  );
}
