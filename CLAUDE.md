# RedPen MVP — Claude Code 작업 지침

RedPen은 새 공시 1건이 흔드는 기관 내부 판단을 찾아 근거와 함께 고치는 AI Agent다. 내부 판단은 가상 문서 4부로 대신한다.
「국부펀드 KIC, AI 에이전트 공모전」 팀 MONDAI의 MVP다. 본선 제출은 2026-12-22(화) 10:00 KST이고, 웹 데모 URL·GitHub 저장소·발표 PDF를 낸다.
배포·API 연동 절차는 @docs/DEPLOY_RUNBOOK.md 를 따른다.

## 명령
- 설치: `python -m venv .venv` → 활성화 → `pip install -r requirements-dev.txt` (Python 3.11+)
- 테스트: `pytest -q` — 19개가 모두 통과해야 커밋·푸시한다.
- 데모: `streamlit run app.py` (http://localhost:8501). 상태 확인은 `/_stcore/health`.
- API(선택): `uvicorn api:app --port 8000` (Swagger: /docs)
- EDGAR 스냅샷 재생성: `SEC_USER_AGENT="팀명 연락처" python scripts/build_snapshot.py --out data/snapshot_live`. 실행할 때 `REDPEN_SNAPSHOT=data/snapshot_live`를 지정한다.

## 구조
- `redpen/workflow.py`: LangGraph 6단계 그래프. Planner → Impact Mapper → Retriever → Calculator → Judge → Verifier·Writer 순서이고, 재탐색(최대 3회)과 재판정 루프가 있다.
- `redpen/rules.py`: 판정 규칙 R0~R4v. `redpen/xbrl.py`: 수치 계산. `redpen/verify.py`: 재대조. `redpen/outputs.py`: 변경 추적 docx와 해시 체인 원장.
- `data/snapshot/`: SEC EDGAR 실데이터 사본(2026-11-11 수집). `data/internal_docs/`: 가상 문서. `data/gold.yaml`: 정답셋 초안.

## 꼭 지킬 것
- IMPORTANT: API 키·토큰·비밀번호를 코드, 커밋, 로그, 대화에 쓰지 않는다. 키 값은 사용자가 직접 넣게 안내한다. 넣는 곳은 로컬 `.streamlit/secrets.toml`(gitignore됨), Streamlit Cloud Secrets, `gh secret set`(값은 사용자가 직접 입력)이다. 형식은 `.streamlit/secrets.toml.example`에 있다.
- 숫자는 XBRL 원값과 코드 계산만 쓴다. LLM이 수치를 만들거나 판정 규칙을 바꾸게 하지 않는다. LLM은 주장 추출, 근거 방향 분류, 수정 문안에만 쓴다.
- 키가 없어도 앱은 규칙 기반으로 동작해야 한다. 기본 데모는 마이크론 공시와 `data/snapshot`이다.
- `data/snapshot/`, `data/internal_docs/`, `data/gold.yaml`은 바꾸지 않는다. 테스트가 이 값에 고정돼 있다. 새 데이터는 별도 폴더에 둔다.
- UI·문서·커밋 메시지는 한국어로 쓴다. 내부 문서는 계속 '가상 문서'로 표시한다.
- 사용자 확인 없이 하지 않는 일: 저장소 공개 전환, force push, 저장소·브랜치·배포 앱 삭제, 유료 API 호출이 많은 작업.
- 개인 이메일 등 개인정보를 커밋하지 않는다. SEC User-Agent에는 팀 공용 연락처를 쓴다.
