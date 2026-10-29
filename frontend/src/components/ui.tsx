// Small shared presentational components.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { IconX } from "./Icons";

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />;
}

export function Loading({ text = "Loading…" }: { text?: string }) {
  return (
    <div className="state-msg">
      <Spinner /> <span>{text}</span>
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string | null | undefined; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div className="error-box" role="alert">
      <span>{error}</span>
      {onRetry && (
        <button className="btn btn-sm" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="state-msg empty">{children}</div>;
}

const TONES: Record<string, string> = {
  // builds
  succeeded: "ok",
  failed: "bad",
  blocked: "bad",
  cancelled: "muted",
  queued: "info",
  running: "info",
  // tasks
  planning: "info",
  verifying: "info",
  needs_review: "warn",
  completed: "ok",
  // proposals
  ready: "ok",
  conflict: "warn",
  applying: "info",
  applied: "ok",
  rejected: "muted",
  superseded: "muted",
  // checks / tools
  passed: "ok",
  not_evaluated: "muted",
  skipped: "muted",
  available: "ok",
  unavailable: "muted",
  needs_auth: "warn",
  incompatible: "warn",
  error: "bad",
  timeout: "bad",
  // roles
  owner: "accent",
  editor: "info",
  viewer: "muted",
};

export function Badge({ status, children, title }: { status: string; children?: ReactNode; title?: string }) {
  const tone = TONES[status] ?? "muted";
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children ?? status.replace(/_/g, " ")}
    </span>
  );
}

export function Tag({ tone = "muted", children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

/** Accessible modal dialog (Escape closes, focus moves inside, focus restored on close). */
export function Modal({
  title,
  onClose,
  children,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const first = el?.querySelector<HTMLElement>("input, textarea, select, button:not([data-close])");
    (first ?? el)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className={`modal ${wide ? "modal-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={ref}
        tabIndex={-1}
      >
        <div className="modal-head">
          <h2 id={titleId}>{title}</h2>
          <button className="icon-btn" aria-label="Close dialog" data-close onClick={onClose}>
            <IconX />
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

/**
 * Two-step destructive action rendered inline (window.confirm is not
 * available in every host).
 */
export function ConfirmButton({
  label,
  confirmLabel = "Confirm",
  question,
  onConfirm,
  className = "btn btn-sm",
  disabled,
  ariaLabel,
  children,
}: {
  label?: string;
  confirmLabel?: string;
  question: string;
  onConfirm: () => void | Promise<unknown>;
  className?: string;
  disabled?: boolean;
  ariaLabel?: string;
  children?: ReactNode;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const yesRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (asking) yesRef.current?.focus();
  }, [asking]);
  if (!asking) {
    return (
      <button className={className} disabled={disabled} aria-label={ariaLabel} onClick={() => setAsking(true)}>
        {children ?? label}
      </button>
    );
  }
  return (
    <span className="inline-confirm" role="group" aria-label={question}>
      <span className="inline-confirm-q">{question}</span>
      <button
        ref={yesRef}
        className="btn btn-sm btn-danger"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await onConfirm();
          } finally {
            setBusy(false);
            setAsking(false);
          }
        }}
      >
        {confirmLabel}
      </button>
      <button className="btn btn-sm" onClick={() => setAsking(false)} disabled={busy}>
        Cancel
      </button>
    </span>
  );
}

/** Simple disclosure menu (button + popover list). */
export function Menu({
  label,
  buttonContent,
  children,
  align = "left",
  buttonClass = "btn",
}: {
  label: string;
  buttonContent: ReactNode;
  children: (close: () => void) => ReactNode;
  align?: "left" | "right";
  buttonClass?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className="menu" ref={ref}>
      <button className={buttonClass} aria-haspopup="true" aria-expanded={open} aria-label={label} onClick={() => setOpen((o) => !o)}>
        {buttonContent}
      </button>
      {open && <div className={`menu-pop menu-${align}`}>{children(() => setOpen(false))}</div>}
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
  className = "",
}: {
  tabs: { id: T; label: ReactNode; title?: string }[];
  value: T;
  onChange: (t: T) => void;
  label: string;
  className?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  return (
    <div className={`tabs ${className}`} role="tablist" aria-label={label}>
      {tabs.map((t, i) => (
        <button
          key={t.id}
          ref={(el) => {
            refs.current[i] = el;
          }}
          role="tab"
          aria-selected={value === t.id}
          tabIndex={value === t.id ? 0 : -1}
          className={`tab ${value === t.id ? "active" : ""}`}
          title={t.title}
          onClick={() => onChange(t.id)}
          onKeyDown={(e) => {
            if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
            e.preventDefault();
            const n = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
            onChange(tabs[n].id);
            refs.current[n]?.focus();
          }}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
