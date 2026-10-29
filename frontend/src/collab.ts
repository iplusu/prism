// Yjs collaboration session for one project. The shared document holds
// `files: Y.Map<path, Y.Text>`; binary files are not part of it.
import { useEffect, useState } from "react";
import { WebsocketProvider } from "y-websocket";
import * as Y from "yjs";
import { api, errorMessage } from "./api";
import type { CollabToken, User } from "./types";
import { colorFor } from "./util";

export type CollabStatus = "connecting" | "synced" | "offline";

export interface Peer {
  clientId: number;
  name: string;
  color: string;
  file?: string;
}

type Listener = () => void;

export class CollabSession {
  readonly doc = new Y.Doc();
  readonly provider: WebsocketProvider;
  readonly files: Y.Map<Y.Text>;
  readonly readonly: boolean;
  status: CollabStatus = "connecting";
  synced = false;
  peers: Peer[] = [];
  private listeners = new Set<Listener>();
  private fileListeners = new Set<Listener>();
  private refreshTimer: number;
  private lastTokenRefresh = 0;
  private destroyed = false;

  constructor(
    private pid: string,
    tok: CollabToken,
    user: User,
  ) {
    this.readonly = !!tok.readonly;
    this.files = this.doc.getMap<Y.Text>("files");
    const url = tok.url || `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/collab`;
    this.provider = new WebsocketProvider(url, tok.room || pid, this.doc, {
      params: { token: tok.token || "" },
      maxBackoffTime: 10000,
      // Keep the collaboration server authoritative (it enforces read-only
      // roles); do not sync between tabs through BroadcastChannel.
      disableBc: true,
    });
    const color = colorFor(user.username);
    this.provider.awareness.setLocalStateField("user", {
      name: user.display_name || user.username,
      color,
      colorLight: color.replace("45%)", "45% / 0.18)"),
    });
    this.provider.on("status", ({ status }: { status: string }) => {
      if (status === "connected") this.setStatus(this.synced ? "synced" : "connecting");
      else if (status === "connecting") this.setStatus("connecting");
      else this.setStatus("offline");
    });
    this.provider.on("sync", (isSynced: boolean) => {
      this.synced = isSynced;
      this.setStatus(isSynced ? "synced" : "connecting");
    });
    // A closed connection may be an expired token (it is valid for 1 h).
    this.provider.on("connection-close", () => {
      this.synced = false;
      void this.refreshToken(false);
    });
    this.provider.awareness.on("change", () => this.updatePeers());
    this.files.observe(() => this.fileListeners.forEach((f) => f()));
    this.refreshTimer = window.setInterval(() => void this.refreshToken(true), 45 * 60 * 1000);
    this.updatePeers();
  }

  private async refreshToken(force: boolean): Promise<void> {
    const now = Date.now();
    if (!force && now - this.lastTokenRefresh < 30_000) return;
    this.lastTokenRefresh = now;
    try {
      const t = await api.collabToken(this.pid);
      if (!this.destroyed && t.enabled && t.token) this.provider.params = { ...this.provider.params, token: t.token };
    } catch {
      /* the provider keeps retrying; status shows "offline" */
    }
  }

  private setStatus(s: CollabStatus) {
    if (this.status === s) return;
    this.status = s;
    this.listeners.forEach((f) => f());
  }

  private updatePeers() {
    const peers: Peer[] = [];
    this.provider.awareness.getStates().forEach((st, clientId) => {
      if (clientId === this.doc.clientID) return;
      const u = (st as { user?: { name?: string; color?: string }; file?: string }).user;
      if (!u?.name) return;
      peers.push({ clientId, name: u.name, color: u.color || "#888", file: (st as { file?: string }).file });
    });
    this.peers = peers;
    this.listeners.forEach((f) => f());
  }

  setActiveFile(path: string | null) {
    this.provider.awareness.setLocalStateField("file", path ?? undefined);
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onFilesChanged(fn: Listener): () => void {
    this.fileListeners.add(fn);
    return () => this.fileListeners.delete(fn);
  }

  destroy() {
    this.destroyed = true;
    window.clearInterval(this.refreshTimer);
    this.provider.destroy();
    this.doc.destroy();
  }
}

export interface CollabState {
  /** undefined while asking the server whether collaboration is enabled */
  enabled: boolean | undefined;
  session: CollabSession | null;
  error: string | null;
}

/** Open a collaboration session for the project when the server enables it. */
export function useCollab(pid: string, user: User): CollabState {
  const [state, setState] = useState<CollabState>({ enabled: undefined, session: null, error: null });
  useEffect(() => {
    let session: CollabSession | null = null;
    let cancelled = false;
    setState({ enabled: undefined, session: null, error: null });
    api
      .collabToken(pid)
      .then((tok) => {
        if (cancelled) return;
        if (!tok.enabled) {
          setState({ enabled: false, session: null, error: null });
          return;
        }
        session = new CollabSession(pid, tok, user);
        setState({ enabled: true, session, error: null });
      })
      .catch((e) => {
        // Without the token we cannot tell; fall back to REST editing, and say why.
        if (!cancelled) setState({ enabled: false, session: null, error: errorMessage(e) });
      });
    return () => {
      cancelled = true;
      session?.destroy();
    };
  }, [pid, user]);
  return state;
}

/** Re-render on session status / peer changes. */
export function useCollabStatus(session: CollabSession | null): { status: CollabStatus | null; peers: Peer[] } {
  const [, force] = useState(0);
  useEffect(() => {
    if (!session) return;
    return session.subscribe(() => force((n) => n + 1));
  }, [session]);
  return { status: session ? session.status : null, peers: session ? session.peers : [] };
}
