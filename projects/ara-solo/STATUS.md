# ARA Solo (Amazon Security) — 제출 패키지

- **상태**: 제출 준비 완료 (4봇 최종 게이트 리뷰 94.0/100 평균, 외부 캘리브레이션 93.75/100 통과)
- **마감**: 2026-11-11 23:59 PT (연장 여부 포털 재확인 권장)
- **포맷**: 공식 10.1.2026 템플릿 3문서, reference IEEE numbered, 과거안 포맷 매칭 (10-29)
- **예산**: cash $80,000 / AWS credits $40,000

## 파일
- `ara-solo-proposal.pdf` — 본문 4p + Appendix A(IEEE refs, 2p) + Appendix B(Previously Funded: 없음 확정)
- `ara-solo-cv.pdf` — 1p, PUBLICATIONS `[FILL]` 6곳 = PI 기입 대기
- `ara-solo-budget.pdf` — bullet 서술형, 10-29 확정 라인항목
- `pilot_n30_semgrep/` — N=30 Semgrep-only 파일럿 (REPORT.md, results.json, code/ 30건, semgrep JSON×2) · 아카이브: `pilot_n30_semgrep_archive_20261101.tar.gz`

## 남은 PI 액션
1. `ara-solo-cv.tex`의 `[FILL]` 6곳에 관련 논문 5-6편 기입 → cv.pdf 재컴파일
2. Amazon Research Awards 포털 제출 (상기 3종 PDF 업로드)

## 게이트 (10-29 실측, 전부 PASS)
본문 4p / 11pt·1인치 / overfull 0 / abstract 200단어 byte-stable / arXiv 0 / App A ≤2p / CV 1p / $80K·$40K 산술 일치

## 게이트 (11-01 재실측 — N=30 Semgrep 파일럿 반영 후, 전부 PASS)
본문 4p (Funds Needed 문단으로 종료) / 11pt·1인치·stretch 0.89 불변 / overfull 0 / abstract byte-stable (md5 6bbaed95…) / arXiv 0 / App A 2p·App B 1p (총 7p) / $80K·$40K 불변
변경: Evaluation 말미에 \textbf{Preliminary Evidence.} 단락 1개 추가 (N=30 Semgrep-only 파일럿 실측 6개) + 분량 상쇄용 중복 25문구 절감 (abstract·게이트 문·예산·hedge 무접촉) — diff: `pilot_n30_semgrep/proposal_diff.txt`

작업용 원본: `/home/ubuntu/grants-wiki/drafts/ara-solo-{proposal,cv,budget}.{tex,pdf}`
