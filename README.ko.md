# Prism — 연구실용 LaTeX 논문 작성 워크스페이스

연구실 서버에서 운영하는 브라우저 기반 논문 작성 도구입니다. 한 화면에서 다음을 제공합니다.

- LaTeX 프로젝트·파일 관리(생성, ZIP 가져오기, 업로드, 이름 변경, 삭제, 검색, 섹션 개요)
- 공식 학회·프리프린트 템플릿(ACL, CVPR/ICCV, NeurIPS 2025, ICML 2026, arXiv): 프로젝트를 만들 때 발행처의 공식 URL에서 내려받으며, Prism이 재배포하지 않습니다
- 참고문헌: arXiv 검색, arXiv id/DOI로 BibTeX 추가(arXiv·doi.org가 주는 항목을 그대로 사용, 중복 감지)
- PDF ↔ 원문 이동(SyncTeX): "Show in PDF"(또는 Ctrl+Alt+J)가 커서가 있는 줄을 PDF 페이지에 강조하고, PDF를 더블클릭하면 해당 줄의 원문 파일이 열립니다(include한 파일도 포함)
- 참고문헌 검증: 모든 `.bib` 항목을 DOI(doi.org), arXiv id(arXiv API), 제목(Crossref, 이어서 OpenAlex)으로 조회해 확인됨 / 다른 저작 / 찾지 못함으로 표시합니다. "찾지 못함"은 참고용입니다(단행본과 영어가 아닌 저작은 자주 빠집니다)
- Zotero 가져오기: 라이브러리, 그룹, 컬렉션을 `.bib`에 이미 있는 저작과 중복되지 않게 병합합니다(API 키는 요청마다 보내고 저장하지 않으며, 공개 그룹은 키가 필요 없습니다)
- arXiv 사전 점검(Check 탭)과 마지막 컴파일의 `.bbl`을 담은 "arXiv source (.zip)" 내보내기. 규칙은 arXiv 도움말 페이지를 따랐습니다
- 리뷰어 보고서(AI 패널, "Reviewer report…"): Codex, Claude Code, Antigravity가 각각 별도의 읽기 전용 리뷰어로 논문을 병렬로 검토합니다(각자의 샌드박스 안, 쓴 내용은 버려짐). 원하면 직접 입력한 페르소나(예: "ACL area chair")로 검토하게 할 수 있습니다. 이어서 총괄 LLM이 리뷰를 합의점, 의견 차이, 우선순위가 붙은 개선 계획이 있는 하나의 보고서로 합치며, 각 도구의 원본 리뷰도 펼쳐 볼 수 있습니다. 실패한 도구는 실패로 표시하고 채워 넣지 않습니다
- AI 분석 프리셋(AI 패널): 용어 일관성, 빠진 인용. 서버가 답변만 허용하며 수정이나 도구 호출은 하지 못하게 합니다
- 아무 `.tex`나 컴파일: Compile은 열려 있는 파일을 빌드하고 PDF 이름도 그 파일을 따릅니다(`\documentclass`가 없는 섹션 파일은 메인 파일을 빌드). 스위치로 항상 메인 파일만 빌드하게 할 수 있습니다
- 드래그 앤 드롭: 파일이나 폴더를 파일 패널 또는 폴더 위에 놓아 업로드합니다. 업로드는 기존 파일을 조용히 덮어쓰지 않습니다. 이미 있는 이름은 모아서 한 번에 바꿀지 유지할지 묻고, 바뀌는 파일은 먼저 프로젝트 기록에 저장됩니다
- 선택 영역에 대해 AI에게 묻기: 편집기에서 텍스트를 선택하고 "Ask AI about selection"을 누르면 선택한 줄이 AI 요청의 문맥이 됩니다
- 빌드 실패: 없는 TeX 패키지(`File 'x.sty' not found`)는 자동으로 설치하고 다시 빌드합니다(`tlmgr`, 공식 TeX Live 저장소만, 빌드당 최대 5개, 스위치 `TEX_PACKAGE_AUTO_INSTALL`, `ALLOW_TEX_PACKAGE_INSTALL`). 대체 수단으로 원클릭 버튼도 있습니다. 그 밖의 LaTeX 오류에는 "Fix with AI"가 있어 검토를 거치는 일반 작업을 만듭니다
- 계획 속도: 외부 도구를 쓰지 않는 단순 답변/수정(`llm` 모드)은 총괄 모델의 숨은 추론을 먼저 끄고(비교 실행에서 2~4배 빠름), 분석, 위임, 도구 선택에서는 켠 채로 둡니다. 잘못된 빠른 계획은 추론을 켜고 다시 만듭니다. `LLM_PLANNING_THINKING=auto|on|off`
- 대화 기억: 각 요청은 프로젝트의 최근 대화(내 요청과 답변, 최대 10개 메시지 / 9,000자, 비밀값 제거, 다른 사용자의 대화는 제외)와 함께 계획되므로 "지적한 내용 반영됐는지 확인해 줘" 같은 후속 요청이 동작합니다. 독립 분석 프리셋은 이 기억 없이 계획됩니다
- AI Assistant의 복사 버튼: 모든 메시지와 각 도구의 원본 리뷰를 보이는 그대로 복사할 수 있습니다. 브라우저가 클립보드를 거부하면 성공한 척하지 않고 "Copy failed"라고 표시합니다
- **AI에게 알려 주는 PDF 페이지 수**: 컴파일마다 PDF 페이지 수를 기록합니다(TeX 로그, 없으면 `pdfinfo`). 계획 단계와 외부 도구에 최근 페이지 수와 그 뒤 텍스트가 바뀌었는지를 알려 주며, 제안 검증은 base → candidate 페이지를 보여 줍니다. 페이지 수가 없으면 추측하지 말고 모른다고 말하게 합니다
- **페이지 한도 추정**: 요청에 한도("8 pages", "8페이지")가 있으면 그 뒤로 대략 몇 줄의 텍스트가 넘어가는지 AI에게 알려 줍니다(페이지별 `pdftotext`, 쪽번호 제외). 추정치이며 그림, 표, float 배치는 계산에 들어가지 않습니다. 검증 보고서에는 마지막 페이지의 텍스트 줄 수가 base → candidate로 나옵니다
- **페이지 한도에 닿을 때까지 재시도**: 요청에 페이지 한도가 있으면 후보를 컴파일하고, 아직 넘으면 같은 실행자(qwen의 직접 수정 또는 선택한 도구)에게 측정된 초과분을 알려 다시 요청합니다. 최대 `PAGE_LIMIT_RETRIES`회(기본 2, 0이면 끔). 한도를 확인했는지는 결과에 그대로 적히며, 아직 넘는 후보를 달성했다고 하지 않습니다
- **AI 문맥의 긴 섹션**: 문맥 예산보다 긴 섹션(또는 섹션 없는 파일)은 통째로 빼지 않고 약 5,000자 조각으로 잘라 넣고, 요청과 맞는 조각을 먼저 넣으며, 무언가 빠졌으면 모델에게 알립니다. 모델이 긴 논문을 얼마나 보는지는 여전히 `LLM_CONTEXT_CHARS`(기본 24000)가 정합니다. 총괄 모델은 훨씬 더 받으므로 긴 원고에서는 올리세요(운영은 400000을 씁니다. 무엇을 시험했고 무엇을 못 했는지는 `docs/verification.md` 참고)
- 원클릭 적용: AI 채팅의 제안에 Apply 버튼이 있고(검증된 제안만), 검토 화면도 한 번 클릭으로 적용합니다
- 자동 컴파일(선택, 기본 꺼짐): 편집을 멈춘 몇 초 뒤 컴파일합니다. 실패하는 텍스트는 반복하지 않고 한 번만 시도합니다
- AI 자동 완성(선택, 기본 꺼짐): 잠시 멈추면 줄 끝에 흐린 인라인 제안이 나타나고 Tab으로 수락, Esc로 닫습니다. 총괄 LLM을 쓰며 사용자당 한 번에 한 요청만 보냅니다
- 원고 검사(AI 미사용): 정의되지 않은 인용·참조, 중복 라벨·키, 없는 그림·입력 파일, TODO 표시를 줄 번호와 함께 표시
- LaTeX/BibTeX 편집기(CodeMirror 6: 구문 강조, 검색/바꾸기, 오류 위치 이동, 자동 저장)
- 실제 컴파일된 PDF 미리보기(TeX Live + latexmk, bubblewrap 격리, pdf.js 렌더링)
- 논문 문맥을 활용한 AI 채팅. 별도 추론 서버(예: a5000)의 **총괄 LLM**이 요청을 분석하고, 필요하면 Codex / Claude Code / Antigravity CLI를 자동으로 호출
- AI 수정 내용의 diff 검토, 검증 보고서, 후보 PDF, 전체/부분 적용, 충돌 해결
- 버전 기록(Git), 이름 붙은 버전, 버전 복원(검토를 거치는 제안으로), ZIP/PDF 내보내기
- 여러 연구자의 실시간 공동 편집(Yjs)과 역할(owner / editor / viewer)

> 첨부된다고 한 `Local_Research_Workspace_Design.md`는 작업 환경에서 찾을 수 없었습니다(전체 파일시스템 검색 결과 없음). 구현은 요청 프롬프트와 이후의 설계 변경(총괄 LLM을 별도 서버로 분리, `LLM_*` 설정)을 기준으로 했습니다.

![workspace](docs/screenshots/03-workspace-compiled.png)

## 구성 요소

| 구성 요소 | 기술 | 위치 |
|---|---|---|
| Web/API | Python 3.12, FastAPI, SQLAlchemy, Alembic | `backend/app` |
| Worker | 같은 코드베이스, `python -m app.worker` | `backend/app/worker.py` |
| DB / 작업 큐 | PostgreSQL 17 (`FOR UPDATE SKIP LOCKED` 큐). 개발용 SQLite 지원 | `backend/migrations` |
| 컴파일 | TeX Live + latexmk, bubblewrap 샌드박스 | `backend/app/compile` |
| 총괄 LLM | OpenAI 호환 API 클라이언트(provider adapter 구조) | `backend/app/llm` |
| 도구 어댑터 | `codex exec`, `claude -p`, `agy -p` | `backend/app/adapters` |
| 공동 편집 | Node + Yjs (y-protocols), 영속 저장 | `collab/server.mjs` |
| Frontend | React 18 + TypeScript + Vite, CodeMirror 6, pdf.js | `frontend/` |
| 배포 | Docker Compose + nginx | `docker-compose.yml`, `deploy/` |

권장 스택과 다른 선택(Redis 대신 PostgreSQL 큐, 빌드별 컨테이너 대신 worker 컨테이너 안의 bubblewrap)과 그 이유는 [docs/architecture.md](docs/architecture.md)에 있습니다.

## 빠른 시작 (개발)

필요: `uv`, Node 20+(npm), `git`, `bubblewrap`, TeX Live(`latexmk`). sudo 없이도 됩니다(이 저장소는 sudo 없는 서버에서 개발·검증했습니다. 설치 방법은 [docs/deployment.md](docs/deployment.md)).

```bash
make install                 # backend(uv sync), frontend(npm ci), collab(npm ci)
cp .env.example .env         # 개발이면 DATABASE_URL 줄을 지우면 SQLite(var/prism.db) 사용
#   PRISM_ADMIN_USERNAME / PRISM_ADMIN_PASSWORD 를 채우면 첫 실행 때 관리자 생성
#   COLLAB_ENABLED=true 로 두면 dev.sh가 공동 편집 서비스도 띄움
make dev                     # API :8000, worker, collab :1234, Vite :5173
```

브라우저: **http://localhost:5173** (Vite가 `/api`, `/collab`을 프록시)

## 서버 실행

### Docker Compose (운영 권장)

```bash
cp .env.example .env         # PRISM_SECRET_KEY, PRISM_ADMIN_*, COLLAB_SHARED_SECRET, LLM_* 입력
export POSTGRES_PASSWORD=...  # 또는 .env 에 추가
docker compose build
docker compose up -d
```

브라우저: **http://<서버>:8080** (`PRISM_HTTP_PORT`로 변경). 외부에 열 때는 TLS 리버스 프록시를 앞에 두고 `PRISM_COOKIE_SECURE=true`. 서비스 구성·네트워크 격리는 [docs/deployment.md](docs/deployment.md).

> 이 개발 호스트에는 Docker가 없어 Compose 구성은 **실행 검증하지 못했습니다**(YAML 구조만 확인). 아래의 Docker 없는 실행 방식은 실제로 검증했습니다.

### Docker 없이

```bash
cd frontend && npm ci && npm run build && cd ..
./scripts/serve.sh           # .env 필요. web(0.0.0.0:8000) + worker + collab
```

브라우저: **http://<서버>:8000**. 공동 편집 WebSocket도 같은 포트(`/collab`)로 프록시되므로 1234 포트는 외부에 열 필요가 없습니다.

### Tailscale로 접속 (tailnet 전용)

공동 편집 WebSocket은 web 프로세스가 같은 포트의 `/collab`으로 프록시하므로(`COLLAB_PUBLIC_URL`은 비워 둠), 포트 하나만 있으면 됩니다. 아래 두 방식 모두 공개·LAN 인터페이스에는 열리지 않습니다.

**포트 없는 HTTPS (권장, 현재 배포 방식)**: Prism은 localhost에서만 받고, Tailscale이 tailnet 인증서로 TLS를 처리합니다.

1. Tailscale 관리 콘솔(DNS 메뉴)에서 MagicDNS와 HTTPS Certificates를 켭니다.
2. 현재 사용자가 Tailscale serve를 관리할 수 있게 한 번 설정합니다(sudo 필요): `sudo tailscale set --operator=$USER`
3. `.env`: `PRISM_HOST=127.0.0.1`, `PRISM_COOKIE_SECURE=true` 후 `prism-web prism-collab prism-worker` 재시작.
4. `tailscale serve --bg --https 443 http://127.0.0.1:8000` (재부팅 후에도 유지, 확인 `tailscale serve status`, 해제 `tailscale serve --https=443 off`).

- 접속: `https://<machine>.<tailnet>.ts.net/`
- Secure 쿠키를 쓰므로 HTTP 주소로는 로그인할 수 없습니다. HTTP 포트를 함께 열지 마십시오.

**포트를 쓰는 HTTP (sudo 불필요)**: `PRISM_HOST=tailscale`이면 `serve.sh`/systemd 서비스가 이 노드의 Tailscale IPv4에만 바인딩합니다. 접속: `http://<machine>.<tailnet>.ts.net:8000` (`PRISM_COOKIE_SECURE=false` 유지). 80 같은 1024 미만 포트는 root가 필요해 주소에 포트가 남습니다.

### systemd 서비스로 상시 실행 (root 불필요)

```bash
loginctl enable-linger $USER          # 로그인 없이도 서비스 유지 (한 번)
./scripts/install-systemd.sh          # prism-db(선택)/web/worker + 매일 03:30 백업 타이머 (.env에 COLLAB_ENABLED=true면 collab도)
systemctl --user start prism-db prism-web prism-worker prism-backup.timer   # collab을 쓰면 prism-collab도
systemctl --user status 'prism-*'     # 상태
journalctl --user -u prism-web -f     # 로그
```

`PRISM_PG_DATA`/`PRISM_PG_BIN`을 설정하면 PostgreSQL도 `prism-db` 서비스로 함께 관리합니다. 설정을 바꾼 뒤에는 `systemctl --user restart prism-web prism-worker`. `COLLAB_ENABLED`를 바꾼 뒤에는 `install-systemd.sh`를 다시 실행하세요. false이면 collab 유닛을 지우고 web이 collab을 부르지 않습니다.

관리 명령:

```bash
cd backend
uv run python -m app.cli create-user alice --admin   # 비밀번호는 프롬프트/stdin
uv run python -m app.cli doctor                      # TeX, 샌드박스, LLM 설정, collab, 도구 상태
uv run python -m app.cli llm-test                    # 총괄 LLM 연결 시험(JSON 출력)
uv run python -m app.cli tools                       # 도구 설치/인증 상태
```

## 총괄 LLM 연결 (a5000 등 별도 추론 서버)

모델은 웹앱 서버에 설치하지 않습니다. 백엔드만 추론 API를 호출하고, 브라우저는 LLM 서버에 직접 접근하지 않습니다. 기존 LLM 서버의 실행 설정은 바꾸지 않습니다.

```ini
LLM_PROVIDER=openai          # OpenAI 호환 /v1/chat/completions (vLLM, SGLang, llama.cpp server, LM Studio …)
LLM_BASE_URL=http://<웹앱 서버에서 접근 가능한 주소>:<포트>/v1
LLM_MODEL=<GET /v1/models 에 나오는 정확한 모델 ID>
LLM_API_KEY=<필요한 경우>
LLM_CONTEXT_CHARS=24000      # 실제 서빙 중인 모델의 문맥 길이에 맞춰 조정 (긴 원고는 훨씬 크게; 운영은 400000)
```

서버 이름만으로 포트·모델 ID·API 형식·문맥 길이를 추측하지 않습니다(배포 시에는 a5000에서 실행 중인 서버와 그 `GET /v1/models` 목록에서 주소와 모델 ID를 확인했습니다). OpenAI 호환이 아닌 API라면 `backend/app/llm/provider.py`의 `LLMProvider`를 구현해 `PROVIDERS`에 등록합니다.

**설정 → Orchestrator LLM → Test connection**(또는 `app.cli llm-test`)이 다음을 실제로 확인합니다.

1. 웹앱 백엔드에서 API 접근 가능 여부(DNS/연결 거부/TLS/인증 오류 구분)
2. 서버가 제공하는 모델 목록과 `LLM_MODEL` 일치 여부(서버가 알려 주면 문맥 길이도 표시)
3. 일반 응답과 스트리밍 응답
4. tool calling 지원 여부, JSON 출력(`json_object`/`json_schema`) 지원 여부
5. 시간 초과 처리(긴 생성 요청에 50 ms 예산 → 깔끔한 `timeout` 오류)

시험 결과는 저장되어, `LLM_PLANNING_MODE=auto`일 때 계획 방식(tool calling → JSON 모드 → 프롬프트 JSON + 서버 검증)을 **관찰된 능력**에 맞춰 고릅니다.

## 외부 CLI 설치·인증

각 도구는 서비스 전용 홈 디렉터리(기본 `<PRISM_DATA_DIR>/tool-homes/<tool>`)를 씁니다. 사용자 개인 홈의 설정·인증은 쓰지 않습니다.

| 도구 | 설치 | 인증(둘 중 하나) |
|---|---|---|
| Codex | `npm install -g @openai/codex` | `CODEX_HOME=<data>/tool-homes/codex/.codex codex login` 또는 `CODEX_OPENAI_API_KEY` |
| Claude Code | `npm install -g @anthropic-ai/claude-code` (또는 공식 설치 스크립트) | `CLAUDE_CONFIG_DIR=<data>/tool-homes/claude/.claude claude auth login`, 또는 `CLAUDE_ANTHROPIC_API_KEY`(이때 `--bare`로 실행) |
| Antigravity | [공식 문서](https://antigravity.google/docs)의 설치 방법 | `HOME=<data>/tool-homes/antigravity agy`로 한 번 대화형 로그인, 또는 `AGY_GEMINI_API_KEY` |

상태는 **설정 → Tools**에서 확인합니다(설치/버전/인증/`--help` 옵션 호환성). 자세한 내용은 [docs/agent-adapters.md](docs/agent-adapters.md).

## 실제 서비스와 DEMO 모드

| | 실제 서비스 | `DEMO_MODE=true` |
|---|---|---|
| 총괄 계획 | 설정된 LLM | LLM 미설정 시 규칙 기반 계획(질문에는 "답할 수 없음"을 명시) |
| 실행기 | Codex / Claude Code / Antigravity | 추가로 `demo` 실행기: 실제 하위 프로세스지만 AI 없이 표시용 주석 한 줄을 넣는 고정 편집 |
| 표시 | — | 모든 화면 상단 배너, 작업·제안에 `DEMO` 태그 |

DEMO 모드에서도 컴파일·검증·diff·적용·공동 편집은 실제로 동작합니다. 운영 인스턴스에서는 켜지 마십시오.

## 데이터 저장 위치와 백업

- DB: PostgreSQL(사용자, 프로젝트, 빌드·작업·제안 메타데이터, 작업 큐, 이벤트)
- `PRISM_DATA_DIR/projects/<id>/repo`: 현재 원고 작업 트리 + `.git` 기록
- `PRISM_DATA_DIR/builds/<id>/output.pdf`: 빌드 PDF(프로젝트별 최근 10개 + 마지막 성공본 유지)
- `PRISM_DATA_DIR/proposals/<id>/{base,candidate}`: 제안의 변경 전/후 파일
- `PRISM_DATA_DIR/tasks/<id>/attempt-N/{work,run}`: 도구 작업 사본과 로그
- `PRISM_DATA_DIR/tool-homes/`: 도구별 로그인 정보(비밀 — 백업 시 주의)
- `COLLAB_DATA_DIR/<id>.ystate`: 공동 편집 문서 상태

```bash
./scripts/backup.sh [대상 디렉터리]     # pg_dump(또는 SQLite 백업) + data + collab, SHA256SUMS 포함
./scripts/restore.sh <백업 디렉터리>    # 서비스 중지 후 실행
```

## 현재 구현 상태

구현 및 검증 완료(자세한 근거는 [docs/verification.md](docs/verification.md)):

- 프로젝트 홈, 작업 화면, 변경 검토, 설정 화면 전체(라이트/다크, 좁은 화면 탭 전환, 패널 크기 조절·접기)
- 안전한 ZIP 가져오기, 프로젝트별 권한, CSRF 방어
- 격리된 실제 LaTeX 컴파일, 실패 시 마지막 성공 PDF 유지, stale 표시
- 총괄 LLM 클라이언트, 연결 시험 5단계, 스키마 검증·재요청, 직접 답변/직접 수정 제안/외부 도구 위임
- 도구 어댑터 3종, 자동 선택 정책, 시도 2회·실행 10분·전체 25분 제한, 취소·재시도, 허용 범위 검사, 재귀 위임 차단
- 제안 검증(컴파일 + 인용/라벨/수치/수식 보고), 조건부 적용, 3-way merge 후 재검증, 충돌 해결, 부분(헝크) 적용 후 재검증
- 실시간 공동 편집(두 클라이언트 동시 편집, AI 변경 반영, 읽기 전용 viewer, 재접속, 재시작 후 복원)
- 버전 저장·비교·복원, ZIP/PDF 내보내기, 백업·복원 스크립트
- 공식 템플릿(5종 모두 실제로 내려받아 가져오고 샌드박스에서 컴파일), arXiv 검색, arXiv id/DOI BibTeX 가져오기(arXiv BibTeX와 doi.org는 실제 서비스로 확인), 원고 검사, AI 자동 완성(실제 a5000이 추론을 끈 상태에서 약 0.6초에 답함, 브라우저로 확인)
- 페이지 한도: 컴파일마다 PDF 페이지 수와 페이지별 텍스트 줄 수를 기록하고, 한도가 있는 요청에는 측정한 초과분을 추정치로 알려 주며, 후보를 다시 컴파일해 최대 `PAGE_LIMIT_RETRIES`번 재시도합니다(실제 pdflatex 시험, 그리고 실제 a5000 실행: 13 → 12쪽, 12 → 11쪽이 한도에 닿음)
- AI 문맥의 긴 원고: 너무 긴 섹션은 버리지 않고 조각으로 자르고, 텍스트가 빠졌으면 모델에게 알립니다(운영 `LLM_CONTEXT_CHARS=400000`, 150쪽 분량 합성 문서도 타임아웃 없이 계획됨)
- solo 모드: `COLLAB_ENABLED=false`이면 프로젝트 저장소(`receive.denyCurrentBranch=updateInstead`)로 들어온 외부 `git push`가 Restore → 제안 → Apply 없이 바로 컴파일됩니다. 이때 `install-systemd.sh`는 collab 서비스를 설치하거나 시작하지 않습니다. 운영 worker에서 임시 프로젝트로 확인했습니다

남은 작업 / 미검증:

- **총괄 LLM**: a5000 서버(vLLM, Qwen3.8-27B-FP8, 모델 ID `qwen38-a5000`)에 연결했습니다. 연결 시험 7단계가 모두 통과했고, 실제 Auto 질문에 프로젝트 내용대로 정확히 답했습니다. 실제 모델이 수정 작업을 계획해 각 외부 도구에 위임하는 것도 확인했습니다(아래). 실제 모델이 수정 제안을 직접 작성하는 것도 확인했습니다(페이지 한도 실행).
- **외부 도구**: Codex 0.165.0과 Claude Code 2.1.312는 서비스 전용 홈에 따로 로그인했고, Antigravity(agy) 1.4.10도 Prism용으로 따로 로그인했습니다(토큰이 개인 계정의 것과 다릅니다). 세 도구 각각 bwrap 샌드박스 안에서 실제 수정 작업으로 제안을 만들었고, Antigravity는 자기 로그인으로 다시 실행했습니다(86초, 파일 1개 변경, 제안 준비됨). Antigravity는 PCLMUL을 지원하는 CPU가 필요합니다(QEMU 기본 CPU 모델로는 부족).
- **실제 LLM으로 Auto 모드**: 처음부터 끝까지 확인했습니다. 작은 요청은 모델이 직접 수정했고, 파일 구조를 바꾸는 요청은 위임했으며 Auto는 Codex를(프로젝트 순서에서 먼저일 때는 Claude Code를) 골랐습니다. 검증이 실제 문제도 잡았습니다(Claude Code는 파일을 삭제하지 못해 수식이 중복됨). 계획 시간은 1분 미만에서 10분 이상까지 달랐고(공유 a5000, `LLM_TIMEOUT_SECONDS=600`) 한 번은 이 제한에 걸려 실패했습니다. **실제 도구로 확인하지 못한 것: 실패 후 다음 도구로 넘어가는 대체 동작**(가짜 CLI 테스트로만 확인).
- **arXiv 검색**: 응답 파싱은 실제 구조와 같은 응답으로 테스트했고, 실제 검색은 시험 당시 arXiv 요청 제한(HTTP 429)에 걸려 확인하지 못했습니다.
- **페이지 한도, 긴 문맥, solo 모드** 중 미검증: 실제 외부 도구로 한 재시도(qwen의 직접 수정과 가짜 CLI만 사용); 그림, 표, float가 많은 문서(줄 수 추정은 이를 계산하지 않아 어느 쪽으로든 틀릴 수 있음); 실제 LaTeX/한국어 텍스트에서의 40만 자 문맥(글자당 토큰이 합성 영문 텍스트보다 많아 모델의 262144 토큰 창에 더 일찍 닿을 수 있음)과 그 계획 시간만 따로 잰 것; 웹에서 편집해 커밋되지 않은 작업 트리에 `git push`하는 경우(`updateInstead`가 거부해야 함)와 solo 모드에서 두 사람이 한 파일을 편집하는 경우; 재부팅 후 상태. 40만 자 실행에서는 최종 메시지가 모델의 설명이 아니라 서버의 기본 요약으로 나왔고 원인은 조사하지 않았습니다. 자세한 내용은 `docs/verification.md`.
- **Docker Compose**: 이 호스트에 Docker가 없어 이미지 빌드·기동을 검증하지 못했습니다.
- 백업 스크립트: `pg_dump` 백업은 운영 PostgreSQL에서 실제로 실행해 확인했고, `restore.sh`의 `pg_restore` 복원 경로는 아직 실행하지 않았습니다. SQLite 백업·복원 경로는 검증했습니다.
- 도구 프로세스의 메모리 상한은 컨테이너(`mem_limit`)에 의존합니다(Node 기반 CLI는 `RLIMIT_AS`와 호환되지 않음).
- AI 채팅 기록은 사용자별입니다(작업 기록은 프로젝트 공유).

## 문서

- [docs/architecture.md](docs/architecture.md) — 구성, 데이터 흐름, 보안 경계, 설계 선택 이유
- [docs/deployment.md](docs/deployment.md) — 설치, Compose, 네트워크, TLS, 운영
- [docs/agent-adapters.md](docs/agent-adapters.md) — 총괄 LLM, 작업 계약, 도구별 옵션·이벤트·인증
- [docs/verification.md](docs/verification.md) — 실제 수행한 검사와 결과
