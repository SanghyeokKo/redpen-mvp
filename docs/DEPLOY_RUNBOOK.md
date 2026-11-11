# RedPen 배포·연동 런북 (Claude Code용)

각 단계는 다음 셋으로 구성된다.

- **할 일**: Claude Code가 수행한다.
- **완료 기준**: 명령 출력, URL, 스크린샷 같은 증거로 보여 준다.
- **사용자 작업**: Claude Code가 대신할 수 없는 일이다. 여기서 멈추고, 무엇을 어디에 입력·클릭해야 하는지 정확히 안내한다.

한 단계의 완료 기준을 충족하기 전에는 다음 단계로 넘어가지 않는다. 실패하면 원인을 고친 뒤 같은 검증을 다시 돌린다.

---

## 0단계. 사전 점검

**할 일**
1. 도구를 확인한다: `python --version`(3.11 이상), `git --version`, `gh --version`, `gh auth status`.
2. 가상환경을 만들고 `pip install -r requirements-dev.txt`로 설치한다. 이어서 `pytest -q`를 실행한다.
3. 앱을 띄워 확인한다.
   - `streamlit run app.py --server.headless true`를 백그라운드로 띄운다.
   - `http://localhost:8501/_stcore/health`가 `ok`인지 확인한다.
   - 확인이 끝나면 프로세스를 종료한다.

**완료 기준**: `19 passed`가 나오고 health가 `ok`다.

**사용자 작업**
- 없는 도구를 설치한다 (Python 3.11+, git, GitHub CLI).
- `gh auth login`으로 로그인한다. 브라우저 인증이므로 사용자가 직접 한다.

## 1단계. GitHub 저장소

**할 일**
1. 저장소 이름과 소유자를 사용자에게 확인한다.
   - 이름 기본값은 `redpen-mvp`다.
   - 소유자는 개인 계정이나 팀 organization 중 하나다.
   - 공개 여부의 기본값은 **private**이다.
2. 커밋 전에 비밀정보를 점검한다.
   - `git grep -nIiE "(sk-[a-z0-9]|api[_-]?key\s*=\s*['\"][^'\"]+|token\s*=|password\s*=)"`로 검색해 걸리는 것이 없어야 한다. 예시 파일의 주석은 예외다.
   - `.streamlit/secrets.toml`, `.env`, `runs/`, `.cache/`가 커밋 대상에서 빠졌는지 `git status --ignored`로 확인한다.
   - 5MB가 넘는 파일이 없는지 확인한다.
3. `git init -b main` 후 첫 커밋을 만든다. 메시지는 한국어로 `RedPen MVP v0.2: 6단계 Agent·Streamlit 데모·테스트`처럼 쓴다.
4. `gh repo create <소유자>/redpen-mvp --private --source . --remote origin --push --description "새 공시 1건이 흔드는 기관 내 판단을 찾아 근거와 함께 고치는 AI Agent (KIC AI 에이전트 공모전 MVP)"`
5. GitHub Actions(`.github/workflows/tests.yml`) 결과를 `gh run watch` 또는 `gh run list --limit 1`로 확인한다.

**완료 기준**: 저장소 URL이 있고, 마지막 Actions 실행이 `success`다.

## 2단계. Streamlit Community Cloud 배포

Community Cloud 배포는 웹 화면에서 한다. Claude Code는 아래 표의 값을 정리해 주고, 사용자가 배포를 마치면 검증을 맡는다.

| 항목 | 값 |
|---|---|
| Repository | `<소유자>/redpen-mvp` |
| Branch | `main` |
| Main file path | `app.py` |
| App URL (서브도메인) | 예: `mondai-redpen` (쓸 수 있는 이름으로) |
| Advanced settings → Python | 3.12 (기본값) 또는 3.11 |
| Advanced settings → Secrets | 비워 둔다 (규칙 기반 데모는 키가 필요 없다) |

**사용자 작업**
1. https://share.streamlit.io 에 GitHub 계정으로 로그인한다. 처음이면 Streamlit의 GitHub 접근 권한을 승인하고, private 저장소 접근도 허용한다.
2. **Create app** → **Yup, I have an app**을 누르고 표의 값을 입력한 뒤 **Deploy**를 누른다.
3. 배포가 끝나면 **Share → Make this app public**을 누른다. private 저장소에서 배포한 앱은 기본이 비공개이고, 비공개 앱은 워크스페이스당 1개만 둘 수 있다. 심사위원은 로그인 없이 봐야 한다.

**할 일 (사용자가 앱 주소를 알려 준 뒤)**
1. 시크릿 창과 같은 비로그인 상태에서 `https://<서브도메인>.streamlit.app`이 열리는지 확인한다. 예: `curl -sL -o /dev/null -w "%{http_code}"`.
2. Playwright가 있으면 첫 화면 스크린샷을 찍어 핵심 수치를 확인한다.
   - 영향 문장 12/19, 수정 4, 확인 필요 3, 유지 5, 충돌 2, 검증 통과.
   - 스크린샷은 `docs/screenshots/deployed_home.png`로 저장한다.
3. README 맨 위에 `**데모:** https://<서브도메인>.streamlit.app` 줄을 추가하고 커밋·푸시한다.
4. 앱이 새 커밋으로 다시 배포됐는지 확인한다. 푸시하면 보통 자동으로 반영된다. 안 되면 사용자에게 Manage app → Reboot를 안내한다.

**완료 기준**: 비로그인 접속이 되고, 스크린샷 수치가 맞고, README에 URL이 있다.

**문제 해결**: 앱 화면 오른쪽 아래 **Manage app**에서 로그를 본다. 저장소 쓰기 권한이 있어야 보인다. 의존성 오류는 `requirements.txt`를 고쳐 푸시해서 해결한다.

---

## 3단계. API 연동 (본선 개발 기간)

이 단계는 plan mode로 계획을 먼저 보여 주고, 사용자가 승인한 뒤 구현한다.
기존 테스트 19개와 규칙 기반 기본 데모는 계속 통과해야 한다.

### 3-1. LLM API

**구현 내용**
- **키 입력**: 키 이름과 형식은 `.streamlit/secrets.toml.example`에 있다. 값은 사용자가 직접 넣는다.
  - 로컬은 `.streamlit/secrets.toml`에 넣는다.
  - 배포 앱은 Settings → Secrets에 넣는다.
  - 최상위 키는 `app.py`의 `_secrets_to_env()`가 환경 변수로 옮긴다.
- **엔진 비교 스크립트**: `scripts/compare_engines.py`를 추가한다.
  - 공시 이벤트마다 규칙 기반과 LLM 모드를 각각 실행한다.
  - 비교 항목: 정답셋 일치율, 판정이 다른 문장, LLM 호출 수, 실행 시간.
  - 결과는 `docs/eval_report.md`에 쓴다.
- **공개 데모 비용 보호**:
  - `REDPEN_LLM_PASSCODE`를 설정하면 접근 코드를 입력한 사람만 LLM 모드를 쓴다 (이미 구현됨).
  - `REDPEN_LLM_MAX_CALLS`로 호출 상한을 둔다 (기본 1000, 이미 구현됨).
  - 실행 결과 캐시(같은 이벤트·같은 모델이면 재사용)를 추가한다.
- **사내 모델 교체**: `REDPEN_LLM_PROVIDER=openai_compatible`와 `REDPEN_LLM_BASE_URL`로 바꿀 수 있다. README에 사용 예를 남긴다.
- **모델 표기**: README에 쓴 모델명·버전·이용 조건을 적는다. 기획서 부록에서 약속한 항목이다.

**완료 기준**
- LLM 모드가 로컬과 배포 앱 양쪽에서 실행된다.
- `docs/eval_report.md`가 생성된다.
- 검증 단계에서 수치 불일치가 0건이다.

### 3-2. SEC EDGAR 실시간 수집

**구현 내용**
1. **User-Agent**: 사용자에게 팀 공용 연락처를 받아 `SEC_USER_AGENT="MONDAI RedPen <연락처>"`로 쓴다. 개인 이메일은 커밋하지 않는다.
2. **로컬 수집과 점검**: `python scripts/build_snapshot.py --out data/snapshot_live`를 실행하고 결과를 점검한다.
   - 회사별 문서 수, MD&A 단락 수, 가이던스 표가 추출됐는지 본다.
   - 파서가 실제 문서 구조에 맞지 않으면 `redpen/edgar.py`를 고친다. 이때 `tests/test_edgar.py`에 그 구조를 반영한 테스트를 추가한다.
3. **결과 비교**: `REDPEN_SNAPSHOT=data/snapshot_live`로 파이프라인을 돌린다. 판정을 `data/snapshot` 기준 결과와 비교해 차이를 설명한다.
4. **앱 데이터 선택**: 사이드바에 데이터 선택을 추가한다.
   - 선택지: `2026-11-11 사본(기본)` / `최신 수집본`.
   - 정답셋 평가 탭은 사본 기준이라고 표시한다.
5. **자동 갱신 워크플로**: `.github/workflows/refresh-snapshot.yml`을 추가한다.
   - 실행: `workflow_dispatch`와 `schedule: "0 22 * * 0-4"`(UTC 기준 = 평일 KST 07:00).
   - `permissions: contents: write`를 준다.
   - Secrets의 `SEC_USER_AGENT`를 쓴다. 사용자가 `gh secret set SEC_USER_AGENT`로 직접 입력한다.
   - 변경이 있을 때만 `data/snapshot_live/`를 커밋·푸시한다. 그러면 Streamlit 앱이 다시 배포된다.
   - `.gitignore`에서 `data/snapshot_live/` 줄을 지운다.
6. **배포 앱 설정**: 배포 앱 Secrets에 `SEC_USER_AGENT`를 넣으면 사이드바의 「실시간 새 공시 확인」 버튼이 동작한다.

**완료 기준**
- 최신 수집본으로 앱이 실행된다.
- 워크플로를 수동 실행해 1회 성공한다 (`gh workflow run` → `gh run watch`).
- 테스트 19개 이상이 통과한다.

### 3-3. REST API (선택)

`api.py`(FastAPI)는 사내 시스템 연동 예시다. 엔드포인트: `GET /events`, `POST /runs/{event_id}`, `GET /ledger`.

- **발표용이면**: 로컬 실행과 Swagger(`/docs`) 화면 캡처로 충분하다.
- **별도 배포가 필요하면**:
  - 배포처: Render, Fly.io, Cloud Run 중 하나를 사용자와 정한다.
  - `Dockerfile`과 API 키 헤더 인증을 추가한 뒤 배포한다.
  - 인증 키 값은 사용자가 플랫폼 Secrets에 넣는다.

---

## 4단계. 본선 제출 전 점검 (12/22 10:00 전)

- [ ] 데모 URL에 비로그인 상태로 들어가 첫 화면이 30초 안에 뜬다.
- [ ] 저장소 접근 방식을 대회 안내대로 정한다: 공개 전환 또는 심사위원 초대. 사용자가 결정한다.
  - 공개 전환 전에 전체 히스토리에서 비밀정보를 다시 점검한다. 예: `git log -p | grep -nIiE "sk-|api[_-]?key|token|password"`, 또는 gitleaks.
- [ ] README에 다음이 있다.
  - 데모 URL과 실행 방법
  - 데이터 출처
  - 생성형 AI 활용 (Claude, Claude Code 사용)
  - 모델명·버전 (LLM 모드를 쓴 경우)
- [ ] `pytest -q`가 통과하고 Actions가 녹색이다.
- [ ] 발표 PDF에 들어갈 화면을 다시 캡처한다: 영향 문장 지도, 근거 카드, 빨간펜 수정안, 충돌 리포트, 실행 로그.
