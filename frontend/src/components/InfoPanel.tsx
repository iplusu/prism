import { api } from "../api";
import { useAsync } from "../util";
import { IconRetry } from "./Icons";
import { Empty, ErrorBox, Loading } from "./ui";

const KO = typeof navigator !== "undefined" && navigator.language.startsWith("ko");

const ROWS: { key: string; en: string; ko: string }[] = [
  { key: "total", en: "Words", ko: "단어 수" },
  { key: "text", en: "Body words", ko: "본문 단어 수" },
  { key: "headers", en: "Header words", ko: "헤더 단어 수" },
  { key: "outside", en: "Words outside text", ko: "본문 외 단어 수" },
  { key: "header_count", en: "Headers", ko: "헤더 개수" },
  { key: "float_count", en: "Floats", ko: "도표 개수" },
  { key: "math_inline", en: "Inline math", ko: "인라인 수식 개수" },
  { key: "math_display", en: "Display math", ko: "디스플레이 수식 개수" },
];

export default function InfoPanel({ pid, refreshKey }: { pid: string; refreshKey: unknown }) {
  const s = useAsync(() => api.stats(pid), [pid, refreshKey]);
  const st = s.data?.stats;
  return (
    <div className="side-panel">
      <div className="side-head">
        <span className="side-title">{KO ? "프로젝트 정보" : "Project info"}</span>
        <div className="spacer" />
        <button className="icon-btn sm" aria-label="Refresh project info" onClick={s.reload}>
          <IconRetry />
        </button>
      </div>
      <ErrorBox error={s.error} onRetry={s.reload} />
      <div className="side-scroll">
        {s.loading && !s.data && <Loading />}
        {s.data && !st && <Empty>{KO ? "단어 수를 계산할 수 없습니다 (texcount 없음)." : "Word count unavailable (texcount not found)."}</Empty>}
        {st && (
          <>
            <h3 className="info-h">{KO ? "요약" : "Summary"}</h3>
            <div className="info-grid">
              {ROWS.map((r) => (
                <div className="info-card" key={r.key}>
                  <div className="info-label">{KO ? r.ko : r.en}</div>
                  <div className="info-value">{(st as unknown as Record<string, number>)[r.key]}</div>
                </div>
              ))}
            </div>
            <p className="side-note small">
              {KO ? "기준 파일: " : "Counted from "}{s.data!.main_file}{KO ? " 및 포함된 파일. 수식은 각각 1단어로 계산." : " and its included files. Each formula counts as one word."}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
