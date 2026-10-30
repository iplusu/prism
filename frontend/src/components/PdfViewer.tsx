// Renders a PDF served by the API to canvases with pdf.js (lazy per page).
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { IconDownload, IconFit, IconZoomIn, IconZoomOut } from "./Icons";
import type { PdfMark } from "../types";
import { ErrorBox, Loading } from "./ui";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

const ZOOMS = [0.5, 0.67, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3];

interface Props {
  url: string;
  downloadName?: string;
  /** Extra header content (build info, badges). */
  header?: ReactNode;
  /** A region to scroll to and flash (from a source position); a new nonce re-triggers it. */
  mark?: PdfMark | null;
  /** Double-click on a page: page number and PDF-point position, for source lookup. */
  onSourceClick?: (page: number, x: number, y: number) => void;
}

export default function PdfViewer({ url, downloadName, header, mark, onSourceClick }: Props) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scale, setScale] = useState<number | "fit">("fit");
  const [fitScale, setFitScale] = useState(1);
  const [page, setPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  const [baseWidth, setBaseWidth] = useState(612);
  const scroller = useRef<HTMLDivElement>(null);

  // A new url (a new build) is loaded in the background: the previous document stays on screen with its
  // scroll position, zoom and page until the new one is ready, then pages are redrawn in place.
  const current = useRef<PDFDocumentProxy | null>(null);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    const task = pdfjs.getDocument({ url, withCredentials: true, isEvalSupported: false });
    task.promise.then(
      async (d) => {
        if (cancelled) {
          void d.destroy();
          return;
        }
        const p1 = await d.getPage(1);
        if (cancelled) {
          void d.destroy();
          return;
        }
        setBaseWidth(p1.getViewport({ scale: 1 }).width);
        const old = current.current;
        current.current = d;
        setDoc(d);
        setPage((n) => Math.min(n, d.numPages));
        if (old) window.setTimeout(() => void old.destroy(), 1000); // after the pages have switched over
      },
      (e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      },
    );
    return () => {
      cancelled = true;
      void task.destroy();
    };
  }, [url]);
  useEffect(
    () => () => {
      void current.current?.destroy();
      current.current = null;
    },
    [],
  );

  // Fit-to-width follows the panel size.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth - 32;
      if (w > 50) setFitScale((cur) => (Math.abs(cur - w / baseWidth) < 0.004 ? cur : Math.max(0.25, w / baseWidth)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [baseWidth]);

  const effScale = scale === "fit" ? fitScale : scale;

  const zoom = (dir: 1 | -1) => {
    const cur = effScale;
    const next = dir > 0 ? ZOOMS.find((z) => z > cur + 0.01) : [...ZOOMS].reverse().find((z) => z < cur - 0.01);
    setScale(next ?? cur);
  };

  const goTo = useCallback(
    (n: number) => {
      if (!doc) return;
      const p = Math.min(Math.max(1, n), doc.numPages);
      const el = scroller.current?.querySelector<HTMLElement>(`[data-page="${p}"]`);
      el?.scrollIntoView({ block: "start" });
      setPage(p);
      setPageInput(String(p));
    },
    [doc],
  );

  // Scroll a requested region into view (re-run when the document is replaced, in case it arrives later).
  useEffect(() => {
    const el = scroller.current;
    if (!mark || !el || !doc) return;
    const pg = el.querySelector<HTMLElement>(`[data-page="${mark.page}"]`);
    if (!pg) return;
    const top = pg.offsetTop + mark.y * effScale - el.clientHeight / 3;
    el.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mark?.nonce, doc]);

  // Track the current page while scrolling.
  const onScroll = () => {
    const el = scroller.current;
    if (!el || !doc) return;
    const mid = el.scrollTop + el.clientHeight / 3;
    let current = 1;
    el.querySelectorAll<HTMLElement>("[data-page]").forEach((p) => {
      if (p.offsetTop <= mid) current = Number(p.dataset.page);
    });
    if (current !== page) {
      setPage(current);
      setPageInput(String(current));
    }
  };

  return (
    <div className="pdf-viewer">
      <div className="pdf-toolbar" role="toolbar" aria-label="PDF controls">
        <div className="pdf-toolbar-info">{header}</div>
        <div className="pdf-pager">
          <button className="icon-btn" aria-label="Previous page" disabled={!doc || page <= 1} onClick={() => goTo(page - 1)}>
            ‹
          </button>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              goTo(Number(pageInput) || 1);
            }}
          >
            <label className="sr-only" htmlFor={`pg-${url}`}>
              Page number
            </label>
            <input
              id={`pg-${url}`}
              className="page-input"
              inputMode="numeric"
              value={pageInput}
              onChange={(e) => setPageInput(e.target.value.replace(/\D/g, ""))}
              disabled={!doc}
            />
          </form>
          <span className="muted small">/ {doc?.numPages ?? "–"}</span>
          <button className="icon-btn" aria-label="Next page" disabled={!doc || page >= (doc?.numPages ?? 0)} onClick={() => goTo(page + 1)}>
            ›
          </button>
        </div>
        <button className="icon-btn" aria-label="Zoom out" onClick={() => zoom(-1)} disabled={!doc}>
          <IconZoomOut />
        </button>
        <span className="muted small zoom-label" aria-live="polite">
          {Math.round(effScale * 100)}%
        </span>
        <button className="icon-btn" aria-label="Zoom in" onClick={() => zoom(1)} disabled={!doc}>
          <IconZoomIn />
        </button>
        <button
          className={`icon-btn ${scale === "fit" ? "active" : ""}`}
          aria-label="Fit width"
          aria-pressed={scale === "fit"}
          onClick={() => setScale("fit")}
          disabled={!doc}
        >
          <IconFit />
        </button>
        <a className="icon-btn" href={url} download={downloadName ?? "document.pdf"} aria-label="Download PDF" title="Download PDF">
          <IconDownload />
        </a>
      </div>
      <div className="pdf-scroll" ref={scroller} onScroll={onScroll} tabIndex={0} aria-label="PDF pages">
        {error && <ErrorBox error={`Could not load the PDF: ${error}`} />}
        {!doc && !error && <Loading text="Loading PDF…" />}
        {doc &&
          Array.from({ length: doc.numPages }, (_, i) => (
            <PdfPage key={i + 1} doc={doc} number={i + 1} scale={effScale} root={scroller.current} mark={mark?.page === i + 1 ? mark : null} onSourceClick={onSourceClick} />
          ))}
      </div>
    </div>
  );
}

function PdfPage({
  doc,
  number,
  scale,
  root,
  mark,
  onSourceClick,
}: {
  doc: PDFDocumentProxy;
  number: number;
  scale: number;
  root: HTMLElement | null;
  mark: PdfMark | null;
  onSourceClick?: (page: number, x: number, y: number) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [pg, setPg] = useState<PDFPageProxy | null>(null);
  const [visible, setVisible] = useState(number <= 2);

  useEffect(() => {
    let cancelled = false;
    doc.getPage(number).then((p) => !cancelled && setPg(p), () => undefined);
    return () => {
      cancelled = true;
    };
  }, [doc, number]);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && setVisible(true), {
      root,
      rootMargin: "600px 0px",
    });
    io.observe(el);
    return () => io.disconnect();
  }, [root]);

  const vp = pg?.getViewport({ scale });

  useEffect(() => {
    if (!pg || !visible || !canvas.current || !vp) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    const c = canvas.current;
    // Draw off screen and copy in one step: resizing the visible canvas would blank it until pdf.js is done.
    const off = document.createElement("canvas");
    off.width = Math.floor(vp.width * dpr);
    off.height = Math.floor(vp.height * dpr);
    const octx = off.getContext("2d");
    if (!octx) return;
    let task: RenderTask | null = pg.render({
      canvasContext: octx,
      viewport: vp,
      transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
    });
    task.promise.then(
      () => {
        c.width = off.width;
        c.height = off.height;
        c.getContext("2d")?.drawImage(off, 0, 0);
      },
      () => undefined,
    );
    return () => task?.cancel();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pg, visible, scale]);

  const w = vp?.width ?? 612 * scale;
  const h = vp?.height ?? 792 * scale;
  return (
    <div
      className="pdf-page"
      data-page={number}
      ref={wrap}
      style={{ width: w, height: h }}
      aria-label={`Page ${number}`}
      role="img"
      title={onSourceClick ? "Double-click to jump to the source" : undefined}
      onDoubleClick={(e) => {
        if (!onSourceClick || !wrap.current) return;
        const r = wrap.current.getBoundingClientRect();
        onSourceClick(number, (e.clientX - r.left) / scale, (e.clientY - r.top) / scale);
      }}
    >
      <canvas ref={canvas} style={{ width: w, height: h }} />
      {mark && (
        <div
          key={mark.nonce}
          className="pdf-mark"
          style={{ left: mark.x * scale - 3, top: mark.y * scale - 2, width: Math.max(mark.w * scale, 12) + 6, height: Math.max(mark.h * scale, 10) + 4 }}
        />
      )}
    </div>
  );
}
