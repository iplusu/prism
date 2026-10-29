# Prism — 연구실용 LaTeX 논문 작성 워크스페이스

연구실 서버에서 운영하는 브라우저 기반 논문 작성 도구입니다. 한 화면에서 다음을 제공합니다.

- LaTeX 프로젝트·파일 관리(생성, ZIP 가져오기, 업로드, 이름 변경, 삭제, 검색, 섹션 개요)
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

`.env`에 `PRISM_HOST=tailscale`을 넣으면 `serve.sh`/systemd 서비스가 이 노드의 Tailscale IPv4에만 바인딩합니다. 공개·LAN 인터페이스에는 열리지 않습니다. 공동 편집 WebSocket은 web 프로세스가 같은 포트의 `/collab`으로 프록시하므로(`COLLAB_PUBLIC_URL`은 비워 둠), 포트 하나만 있으면 됩니다.

- 접속: `http://<machine>.<tailnet>.ts.net:8000`
- HTTPS가 필요하면 tailnet에서 HTTPS 인증서를 켜고 `sudo tailscale set --operator=$USER` 한 번 실행한 뒤 `tailscale serve --bg 8000`을 쓰면 됩니다(이 경우 `PRISM_HOST=127.0.0.1`, `PRISM_COOKIE_SECURE=true`).

### systemd 서비스로 상시 실행 (root 불필요)

```bash
loginctl enable-linger $USER          # 로그인 없이도 서비스 유지 (한 번)
./scripts/install-systemd.sh          # prism-db(선택)/collab/web/worker + 매일 03:30 백업 타이머
systemctl --user start prism-db prism-collab prism-web prism-worker prism-backup.timer
systemctl --user status 'prism-*'     # 상태
journalctl --user -u prism-web -f     # 로그
```

`PRISM_PG_DATA`/`PRISM_PG_BIN`을 설정하면 PostgreSQL도 `prism-db` 서비스로 함께 관리합니다. 설정을 바꾼 뒤에는 `systemctl --user restart prism-web prism-worker`.

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
LLM_CONTEXT_CHARS=24000      # 실제 서빙 중인 모델의 문맥 길이에 맞춰 조정
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

남은 작업 / 미검증:

- **총괄 LLM**: a5000 서버(vLLM, Qwen3.8-27B-FP8, 모델 ID `qwen38-a5000`)에 연결했습니다. 연결 시험 7단계가 모두 통과했고, 실제 Auto 질문에 프로젝트 내용대로 정확히 답했습니다. 실제 모델로 아직 실행하지 않은 것: LLM이 직접 작성한 수정 제안, 외부 도구 위임(도구 미인증).
- **도구 인증**: Codex·Claude Code는 설치·옵션·샌드박스 실행·미인증 감지까지 실제 CLI로 확인했고, 인증된 실제 작업 실행은 서비스용 자격증명이 필요합니다. Antigravity CLI는 이 호스트에 없어 공식 문서 기준으로 구현했으며 실행 검증은 하지 못했습니다.
- **Docker Compose**: 이 호스트에 Docker가 없어 이미지 빌드·기동을 검증하지 못했습니다.
- PostgreSQL `pg_dump`/`pg_restore` 경로의 백업 스크립트(이 호스트에 클라이언트 도구 없음). SQLite 경로는 검증.
- 도구 프로세스의 메모리 상한은 컨테이너(`mem_limit`)에 의존합니다(Node 기반 CLI는 `RLIMIT_AS`와 호환되지 않음).
- AI 채팅 기록은 사용자별입니다(작업 기록은 프로젝트 공유).

## 문서

- [docs/architecture.md](docs/architecture.md) — 구성, 데이터 흐름, 보안 경계, 설계 선택 이유
- [docs/deployment.md](docs/deployment.md) — 설치, Compose, 네트워크, TLS, 운영
- [docs/agent-adapters.md](docs/agent-adapters.md) — 총괄 LLM, 작업 계약, 도구별 옵션·이벤트·인증
- [docs/verification.md](docs/verification.md) — 실제 수행한 검사와 결과
