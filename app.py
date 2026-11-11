"""RedPen MVP — 새 공시 1건이 흔드는 기관 내 판단을 찾아 근거와 함께 고치는 AI Agent (Streamlit 데모)."""
from __future__ import annotations

import hmac
import html
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from redpen.eval import evaluate, load_gold
from redpen.llm import LLMClient
from redpen.outputs import Ledger, build_redline, decision_entry, label3
from redpen.topics import TOPICS
from redpen.workflow import NODE_KO, load_context, run

ROOT = Path(__file__).parent
DATA = ROOT / "data"
LEDGER_PATH = Path(os.getenv("REDPEN_LEDGER", ROOT / "runs" / "ledger.jsonl"))
DEFAULT_EVENT_COMPANY = "MU"  # 데모 시나리오: 공급사의 새 분기 공시

# 판정 색 (선, 바탕). 강조색은 '수정'의 빨간펜 하나만 쓰고 나머지는 채도를 낮춘다.
VC = {
    "수정": ("#B3261E", "#FBEDEB"), "확인 필요": ("#8A6100", "#F8F2E2"), "유지": ("#3D6656", "#ECF2EF"),
    "영향 없음": ("#9AA1AE", "#F4F5F7"), "충돌": ("#14213D", "#EEF1F6"),
}
VERDICT_ORDER = ["수정", "확인 필요", "유지"]
STANCE_KO = {1: "같은 방향", -1: "반대", 0: "참고"}
REL_TITLE = {"same": "같은 회사", "supplier_to_customer": "공급사 → 고객사", "customer_to_supplier": "고객사 → 공급사",
             "competitor": "경쟁사", "sector": "섹터 전체"}
REL_ORDER = ["same", "supplier_to_customer", "customer_to_supplier", "competitor", "sector"]

st.set_page_config(page_title="RedPen · 판단 빨간펜 Agent", page_icon="🖍️", layout="wide",
                   initial_sidebar_state="expanded")


def _secrets_to_env():
    """Streamlit Secrets(배포) 또는 .streamlit/secrets.toml(로컬)의 최상위 값을 환경 변수로 옮긴다.
    redpen 패키지는 환경 변수만 읽으므로, 키를 코드에 쓰지 않고 Secrets에만 두면 된다. 이미 있는 환경 변수는 유지."""
    try:
        for k, v in st.secrets.items():
            if isinstance(v, (str, int, float)) and k not in os.environ:
                os.environ[k] = str(v)
    except Exception:  # noqa: BLE001 - secrets 파일이 없으면 그냥 넘어간다
        pass


_secrets_to_env()

st.markdown("""
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css");
@import url("https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap");
:root {
  --ink:#14213D; --text:#252E3F; --muted:#525C6F; --faint:#7D8697; --rule:#E2E5EA; --panel:#F6F7F9;
  --red:#B3261E; --red-bg:#FBEDEB; --ochre:#8A6100; --ochre-bg:#F8F2E2; --sage:#3D6656; --sage-bg:#ECF2EF;
  --sans:"Pretendard Variable", Pretendard, "Noto Sans KR", "Noto Sans CJK KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
  --serif:"Source Serif 4", "Noto Serif KR", Georgia, serif;
}
.stApp, .stApp p, .stApp li, .stApp label, .stApp button, .stApp input, .stApp textarea, .stApp h1, .stApp h2,
.stApp h3, .stApp h4, .stApp h5, .stApp td, .stApp th {font-family: var(--sans);}
.stApp {color: var(--text);}
header[data-testid="stHeader"] {background: transparent;}
.block-container {max-width: 1240px; padding-top: 2.6rem; padding-bottom: 5rem;}
section[data-testid="stSidebar"] {border-right: 1px solid var(--rule);}

/* 상단: 브랜드 줄 */
.rp-top {display:flex; justify-content:space-between; align-items:baseline; gap:24px; padding-bottom:14px;
  border-bottom:1px solid var(--rule); margin-bottom:26px;}
.rp-brand {display:flex; align-items:baseline; gap:14px;}
.rp-logo {font-size:22px; font-weight:800; color:var(--ink); letter-spacing:-0.4px;}
.rp-logo em {font-style:normal; color:var(--red);}
.rp-tag {font-size:14px; color:var(--muted);}
.rp-prov {font-size:12.5px; color:var(--faint); text-align:right; line-height:1.5;}

/* 히어로: 공시 → 결과 한 문장 → 19문장 막대 */
.rp-event {font-size:15px; color:var(--muted); margin-bottom:4px;}
.rp-event b {color:var(--ink); font-weight:700;}
.rp-docs {font-size:13.5px; color:var(--muted); margin-bottom:10px;}
.rp-docs a {color:var(--muted); text-decoration:underline; text-decoration-color:#C9CED6; text-underline-offset:3px;}
.rp-event a {color:var(--muted); text-decoration:underline; text-decoration-color:var(--rule); text-underline-offset:3px;}
.rp-headline {font-size:30px; font-weight:700; color:var(--ink); letter-spacing:-0.6px; line-height:1.3; margin:2px 0 20px 0;}
.rp-headline .n {font-variant-numeric: tabular-nums;}
.rp-bar {display:flex; gap:3px; height:12px; margin-bottom:10px; max-width:860px;}
.rp-bar span {display:block; height:100%; border-radius:3px;}
.rp-legend {display:flex; flex-wrap:wrap; gap:26px; font-size:14px; color:var(--text); margin-bottom:22px;}
.rp-legend .k {display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:7px; vertical-align:0;}
.rp-legend b {font-variant-numeric: tabular-nums; font-weight:700; color:var(--ink); margin-left:4px;}
.rp-facts {display:flex; flex-wrap:wrap; gap:36px; padding:14px 0 4px 0; border-top:1px solid var(--rule); margin-bottom:8px;}
.rp-fact .l {font-size:12.5px; color:var(--muted);}
.rp-fact .v {font-size:15px; color:var(--ink); font-weight:600;}

/* 판정 표시 */
.badge {display:inline-block; font-size:13px; font-weight:700; padding:3px 9px; border-radius:6px; line-height:1.35; white-space:nowrap;}
.badge.수정 {color:var(--red); background:var(--red-bg);} .badge.확인 {color:var(--ochre); background:var(--ochre-bg);}
.badge.유지 {color:var(--sage); background:var(--sage-bg);} .badge.없음 {color:var(--faint); background:var(--panel);}
.cfb {display:inline-block; font-size:12.5px; font-weight:700; color:var(--ink); background:#E9EDF4; padding:1px 8px;
  border-radius:5px; margin-left:8px; vertical-align:1px; white-space:nowrap;}
/* 영향 지도: 관계별 묶음 + 한 줄 한 문장 */
.grp {margin:4px 0 26px 0;}
.grp-h {display:flex; align-items:baseline; justify-content:space-between; padding-bottom:8px; border-bottom:2px solid var(--ink);}
.grp-h .t {font-size:18px; font-weight:700; color:var(--ink);}
.grp-h .t span {font-size:15px; font-weight:500; color:var(--muted); margin-left:10px;}
.grp-h .n {font-size:14px; color:var(--muted);}
.row {display:grid; grid-template-columns: 96px minmax(0,1fr) 230px; gap:18px; align-items:baseline;
  padding:12px 0; border-bottom:1px solid var(--rule);}
.row .txt {font-size:16px; line-height:1.6; color:var(--ink);}
.row .src {font-size:13.5px; line-height:1.5; color:var(--muted);}
.row .src b {color:var(--text); font-weight:600;}
@media (max-width: 900px) { .row {grid-template-columns: 1fr; gap:4px;} }
.vt {display:inline-block; min-width:62px; font-size:12.5px; font-weight:700;}
.vt.수정 {color:var(--red);} .vt.확인 {color:var(--ochre);} .vt.유지 {color:var(--sage);} .vt.없음 {color:var(--faint);}
.sid {font-size:12px; color:var(--faint); font-variant-numeric: tabular-nums; margin-right:8px;}
.mark-수정 {text-decoration: underline wavy var(--red); text-decoration-thickness:1.3px; text-underline-offset:5px;}
.mark-확인 {text-decoration: underline dotted var(--ochre); text-decoration-thickness:1.6px; text-underline-offset:5px;}
.cf {font-size:12px; color:var(--ink); font-weight:600; margin-left:6px; white-space:nowrap;}

/* 영향 지도: 관계별 열 */
.rp-lead {font-size:16px; color:var(--text); line-height:1.75; margin:6px 0 22px 0; max-width:920px;}
.rp-lead b {color:var(--ink); font-weight:600;}
.lanes {display:grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap:18px; margin-bottom:14px;}
.lane {background:var(--panel); border-radius:10px; padding:16px 18px 10px 18px;}
.lane-h {font-size:16px; font-weight:700; color:var(--ink); margin-bottom:2px;}
.lane-s {font-size:12.5px; color:var(--muted); margin-bottom:12px;}
.ldoc {margin-bottom:14px;}
.ldoc-h {font-size:13px; color:var(--muted); padding-bottom:6px; border-bottom:1px solid var(--rule); margin-bottom:6px;}
.ldoc-h b {color:var(--text); font-weight:600;}
.lrow {display:grid; grid-template-columns: 70px 1fr; gap:2px; font-size:14px; line-height:1.6; padding:6px 0; color:var(--text);}
.rp-none {font-size:14px; color:var(--muted); margin:0 0 34px 0;}

/* 근거·수정안 */
.sec-h {font-size:18px; font-weight:700; color:var(--ink); margin:12px 0 4px 0;}
.sec-s {font-size:13.5px; color:var(--muted); margin-bottom:12px;}
.claim-h {font-size:14px; color:var(--muted); margin-bottom:10px; line-height:1.9;}
.claim-h b {color:var(--text); font-weight:600;}
.sent {font-size:17px; line-height:1.75; color:var(--ink); margin:2px 0 10px 0;}
.redline del {color:#8C93A1; text-decoration-color:#8C93A1;}
.redline ins {display:block; color:var(--red); text-decoration:none; font-weight:600; margin-top:4px;}
.redline ins::before {content:"수정안  "; font-size:12px; font-weight:700; letter-spacing:0.2px; color:var(--red); opacity:0.75;}
.reason {font-size:15px; color:var(--text); line-height:1.7; margin:8px 0;}
.meta {font-size:13.5px; color:var(--muted); line-height:1.8;}
.ev {padding:14px 0 16px 0; border-bottom:1px solid var(--rule);}
.ev:last-child {border-bottom:none;}
.ev-h {display:flex; justify-content:space-between; gap:12px; font-size:13.5px; color:var(--muted); margin-bottom:6px;}
.ev-h .src {color:var(--text); font-weight:600;}
.ev-h a {color:var(--muted); white-space:nowrap;}
.lab {display:inline-block; font-size:12.5px; font-weight:700; padding:1px 7px; border-radius:4px; margin-right:6px;
  background:#EDF0F4; color:var(--ink);}
.dir {font-size:13px; font-weight:700; margin-right:8px;}
.dir.p {color:var(--sage);} .dir.n {color:var(--red);} .dir.z {color:var(--faint);}
.ev-q {font-family:var(--serif); font-size:16px; line-height:1.65; color:var(--text);}
.ev-m {font-size:15.5px; color:var(--ink); font-weight:700; font-variant-numeric: tabular-nums;}
.ev-f {font-size:13px; color:var(--muted); font-variant-numeric: tabular-nums; margin-top:4px; line-height:1.6;}
details.more {margin-top:6px;}
details.more summary {cursor:pointer; font-size:14px; color:var(--ink); font-weight:600; list-style:none; padding:4px 0;}
details.more summary::-webkit-details-marker {display:none;}
details.more summary::before {content:"+ "; color:var(--muted);}
details.more[open] summary::before {content:"− ";}
.item {padding:20px 0 6px 0; border-top:1px solid var(--rule);}
.status {font-size:13px; color:var(--muted); padding-top:8px;}
.status b {color:var(--ink);}

/* 충돌 */
.cf-h {font-size:18px; font-weight:700; color:var(--ink); margin:6px 0 2px 0;}
.side-h {font-size:13px; color:var(--muted); margin-bottom:6px;}
.side-h b {font-size:15px; color:var(--ink);}
.side-row {font-size:16px; line-height:1.65; padding:10px 0; border-top:1px solid var(--rule); color:var(--ink);}
.side-row .who {display:block; font-size:13.5px; color:var(--muted); margin-top:2px;}
.balance {display:flex; height:8px; border-radius:4px; overflow:hidden; background:var(--rule); margin:12px 0 6px 0; max-width:520px;}

/* 로그 */
.flow {display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin:6px 0 10px 0; font-size:13px;}
.flow .nd {padding:3px 9px; border-radius:5px; background:#EEF1F6; color:var(--ink); font-weight:600;}
.flow .nd.again {background:var(--ochre-bg); color:var(--ochre);}
.flow .ar {color:var(--faint);}
.log {display:grid; grid-template-columns: 70px 150px minmax(0,1fr); gap:12px; font-size:14.5px; line-height:1.6; padding:9px 0; border-bottom:1px solid #EEF0F3;}
.log .tm {color:var(--faint); font-variant-numeric: tabular-nums;}
.log .nm {color:var(--ink); font-weight:600;}
.small {font-size:13.5px; color:var(--muted);}
.metric-grid {display:grid; grid-template-columns: repeat(3, 1fr); gap:0; border-top:1px solid var(--rule); margin:8px 0 20px 0;}
.metric-grid div {padding:14px 4px; border-bottom:1px solid var(--rule);}
.metric-grid .l {font-size:13px; color:var(--muted);} .metric-grid .v {font-size:22px; font-weight:700; color:var(--ink);}
.metric-grid .s {font-size:12px; color:var(--faint);}
</style>
""", unsafe_allow_html=True)


def esc(x) -> str:
    return html.escape(str(x))


def vtag(verdict: str) -> str:
    cls = {"수정": "수정", "확인 필요": "확인", "유지": "유지"}.get(verdict, "없음")
    return f'<span class="badge {cls}">{esc(verdict)}</span>'


def mark_cls(verdict: str) -> str:
    return {"수정": "mark-수정", "확인 필요": "mark-확인"}.get(verdict, "")


# ---------------------------------------------------------------- 상태
@st.cache_resource(show_spinner=False)
def get_context(engine: str):
    llm = LLMClient.from_env() if engine == "llm" else None
    return load_context(DATA, llm=llm, ledger_path=LEDGER_PATH)


@st.cache_data(show_spinner=False)
def get_gold():
    return load_gold(DATA / "gold.yaml")


llm_available = LLMClient.from_env() is not None
ss = st.session_state
ss.setdefault("decisions", {})
ss.setdefault("result", None)

# ---------------------------------------------------------------- 사이드바
with st.sidebar:
    st.markdown('<div class="rp-logo">Red<em>Pen</em></div>'
                '<div class="small" style="margin:2px 0 18px 0">판단 빨간펜 AI Agent · MVP</div>', unsafe_allow_html=True)
    ctx_rules = get_context("rules")
    events = ctx_rules.store.events()
    default_idx = next((i for i, e in enumerate(events) if e["company"] == DEFAULT_EVENT_COMPANY), 0)
    ev_idx = st.radio("새 공시", list(range(len(events))), index=default_idx,
                      format_func=lambda i: f"{events[i]['name_ko']} ({events[i]['role']})",
                      captions=[f"{e['members'][0]['form']} · {e['filed']}" for e in events])
    engine_opt = st.radio("판정 엔진", ["rules", "llm"], index=0,
                          format_func=lambda x: "규칙 기반" if x == "rules" else "LLM 보조",
                          captions=["API 키 없이 실행", "키 필요 · 수치는 항상 코드 계산"])
    if engine_opt == "llm" and not llm_available:
        st.caption("LLM 키가 없어 규칙 기반으로 실행합니다. 키 설정 방법은 .streamlit/secrets.toml.example에 있습니다.")
        engine_opt = "rules"
    passcode = os.getenv("REDPEN_LLM_PASSCODE", "")
    if engine_opt == "llm" and passcode:  # 공개 데모에서 API 비용 보호
        code = st.text_input("LLM 모드 접근 코드", type="password", key="llm_code")
        if not hmac.compare_digest(code.encode(), passcode.encode()):
            st.caption("접근 코드를 입력해야 LLM 모드가 켜집니다. 그 전에는 규칙 기반으로 실행합니다.")
            engine_opt = "rules"
    run_clicked = st.button("Agent 다시 실행", type="primary", width="stretch")
    st.markdown('<div style="height:18px"></div>', unsafe_allow_html=True)
    with st.expander("실시간 새 공시 확인"):
        st.caption("SEC_USER_AGENT(이름·연락처)가 설정돼 있을 때만 SEC EDGAR를 조회합니다.")
        if st.button("지금 확인", key="live_check", width="stretch"):
            try:
                from redpen.edgar import EdgarClient, check_new_filings
                new = check_new_filings(ctx_rules.store, EdgarClient(cache_dir=ROOT / ".cache" / "edgar"))
                if new:
                    for n in new:
                        st.markdown(f"- {n['company']} {n['form']} {n['filingDate']} (`{n['accessionNumber']}`)")
                    st.caption("반영하려면 scripts/build_snapshot.py로 사본을 다시 만듭니다.")
                else:
                    st.success("사본 이후 새 실적 공시가 없습니다.")
            except Exception as e:  # noqa: BLE001
                st.warning(f"조회하지 못했습니다: {type(e).__name__}: {e}")

ctx = get_context(engine_opt)
event = events[ev_idx]
gold = get_gold()


def do_run():
    with st.sidebar, st.status("Agent 실행 중", expanded=False) as status:
        def on_step(node, update):
            status.update(label=f"{NODE_KO[node].split(' (')[0]} 완료")
        res = run(ctx, event["id"], on_step=on_step, record=True)
        status.update(label=f"실행 완료: {len(res['node_path'])}단계, {res['elapsed_s']}초", state="complete")
    ss.result = res
    ss.decisions = {}


if run_clicked or ss.result is None or ss.result["event"]["id"] != event["id"] or ss.result["engine"] != engine_opt:
    do_run()
res = ss.result
verdicts, claims, evidence = res["verdicts"], res["claims"], res["evidence"]
docs_meta = {d.id: d for d in ctx.docs}
doc_order = [d.id for d in ctx.docs]
counts = {k: sum(1 for v in verdicts.values() if v["verdict"] == k) for k in ("수정", "확인 필요", "유지", "영향 없음")}
in_conflict = {x: g for g in res["conflicts"] for p in g["pairs"] for x in p}
n_imp = len(res["impacted"])
vf = res["verification"]
ordered = sorted(res["impacted"], key=lambda c: (VERDICT_ORDER.index(verdicts[c]["verdict"]), c))

# ---------------------------------------------------------------- 상단 (히어로)
form_ko = {"10-K": "연간보고서 10-K", "10-Q": "분기보고서 10-Q", "8-K": "실적 보도자료 8-K"}
links = ", ".join(f'<a href="{esc(m["url"])}" target="_blank">{form_ko.get(m["form"], m["form"])} ({esc(m["filed"])})</a>'
                  for m in event["members"] if m["in_snapshot"])
bar = "".join(f'<span style="flex:{counts[k]};background:{VC[k][0] if k != "영향 없음" else "#DDE1E7"}"></span>'
              for k in ("수정", "확인 필요", "유지", "영향 없음") if counts[k])
legend = "".join(f'<span><span class="k" style="background:{VC[k][0] if k != "영향 없음" else "#DDE1E7"}"></span>'
                 f'{k}<b>{counts[k]}</b></span>' for k in ("수정", "확인 필요", "유지", "영향 없음"))
st.markdown(f"""
<div class="rp-top">
  <div class="rp-brand"><span class="rp-logo">Red<em>Pen</em></span>
  <span class="rp-tag">새 공시가 흔든 내부 판단을 찾아, 근거와 함께 고칩니다</span></div>
  <div class="rp-prov">공시·XBRL: SEC EDGAR 실데이터 (2026-11-11 사본)<br>내부 문서: 가상 문서 4부 (데모용)</div>
</div>
<div class="rp-event">새 공시 &nbsp;<b>{esc(event['name_ko'])}({esc(event['role'])}) {esc(event['fiscal'])} 실적</b></div>
<div class="rp-docs">{links}</div>
<div class="rp-headline">내부 판단 <span class="n">{len(claims)}</span>문장 중 <span class="n">{n_imp}</span>문장이 이 공시와 연결됐습니다</div>
<div class="rp-bar">{bar}</div>
<div class="rp-legend">{legend}</div>
<div class="rp-facts">
  <div class="rp-fact"><div class="l">팀 간 충돌</div><div class="v">{len(res['conflicts'])}건 (문장 쌍 {sum(len(g['pairs']) for g in res['conflicts'])}개)</div></div>
  <div class="rp-fact"><div class="l">검증</div><div class="v">{"통과" if vf["passed"] else "실패"}, 인용 {vf['quotes_checked']}건과 수치 {vf['numbers_checked']}건 원문 대조</div></div>
  <div class="rp-fact"><div class="l">재탐색</div><div class="v">{sum(1 for a in res['attempts'].values() if a > 1)}문장, 최대 {max(res['attempts'].values() or [0])}회</div></div>
  <div class="rp-fact"><div class="l">판정 엔진</div><div class="v">{esc(res['engine_label'])}</div></div>
</div>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- 공통 렌더러
def evidence_html(e: dict) -> str:
    lab = label3(e)
    d = {1: ("p", "같은 방향"), -1: ("n", "반대"), 0: ("z", "참고")}[e["stance"]]
    name = ctx.store.name_ko(e["company"])
    if e.get("metric"):
        src, where = f'{name} {"XBRL" if e["kind"] == "xbrl" else "가이던스 계산"}', ""
    else:
        src = f'{name} {e["form"]}'
        where = e["location"].split("›", 1)[-1].strip() if "›" in e["location"] else e["location"]
    head = (f'<div class="ev-h"><span><span class="lab">{lab}</span><span class="dir {d[0]}">{d[1]}</span>'
            f'<span class="src">{esc(src)}</span> {esc(where)}</span>'
            f'<a href="{esc(e["url"])}" target="_blank">SEC 원문</a></div>')
    if e.get("metric"):
        m = e["metric"]
        body = f'<div class="ev-m">{esc(e["label"])} {esc(m.get("period", ""))}: {esc(e["basis"])}</div>'
        body += f'<div class="ev-f">계산식 {esc(m["formula"])}'
        if m.get("prev_formula"):
            body += f'<br>전년 동기 {esc(m["prev_period"])}: {esc(m["prev_formula"])} = {m["prev_value"]}%'
        if m.get("derived"):
            body += "<br>4분기 값은 연간 값에서 3분기 누계를 빼서 계산"
        body += "</div>"
    else:
        body = f'<div class="ev-q">“{esc(e["quote"])}”</div>'
    found = "수치 계산" if e["attempt"] == 0 else f"{e['attempt']}차 탐색에서 찾음"
    foot = f'<div class="ev-f">{esc(e["id"])}, {found}, 접수번호 {esc(e["accession"])}</div>'
    return f'<div class="ev">{head}{body}{foot}</div>'


def evidence_block(cid: str, show: int = 3) -> str:
    v = verdicts[cid]
    evs = [e for e in evidence.get(cid, []) if e["id"] in v["evidence_ids"]]
    evs.sort(key=lambda e: (e["stance"] == 0, e["kind"] == "text"))
    if not evs:
        return '<div class="meta">연결된 근거가 없습니다.</div>'
    head, rest = evs[:show], evs[show:]
    out = "".join(evidence_html(e) for e in head)
    if rest:
        out += (f'<details class="more"><summary>근거 {len(rest)}건 더 보기</summary>'
                + "".join(evidence_html(e) for e in rest) + "</details>")
    return out


def sentence_html(cid: str) -> str:
    c, v = claims[cid], verdicts[cid]
    if v["verdict"] == "수정" and v.get("revision") and ss.decisions.get(cid) != "rejected":
        return f'<div class="sent redline"><del>{esc(c["text"])}</del><ins>{esc(v["revision"])}</ins></div>'
    note = ' <span class="small">(수정안 반려됨)</span>' if ss.decisions.get(cid) == "rejected" else ""
    return f'<div class="sent">{esc(c["text"])}{note}</div>'


def claim_head(cid: str) -> str:
    c, v = claims[cid], verdicts[cid]
    d = docs_meta[c["doc_id"]]
    kind = "전망 문장" if c["modality"] == "forecast" else "사실 전제"
    topic = TOPICS[c["topic"]].label if c["topic"] else ""
    cf = f'<span class="cfb">팀 간 충돌 {in_conflict[cid]["id"]}</span>' if cid in in_conflict else ""
    return (f'<div class="claim-h">{vtag(v["verdict"])} &nbsp;<b>{esc(d.title)}</b> &nbsp;{esc(d.team)}{cf}'
            f'<br>{esc(topic)}에 관한 {kind}, 문장 {cid}</div>')


def trace_text(cid: str) -> str:
    steps = []
    for t in res["traces"].get(cid, []):
        chg = []
        if t.get("added_corpus"):
            chg.append("범위에 " + ", ".join(t["added_corpus"]) + " 추가")
        if t.get("added_terms"):
            chg.append("검색어 " + ", ".join(t["added_terms"]) + " 추가")
        steps.append(f'{t["attempt"]}차' + (f'({"; ".join(chg)})' if chg else "") + f' 새 근거 {t["new"]}건')
    return " → ".join(steps)


def record_decision(cid: str, decision: str):
    ss.decisions[cid] = decision
    Ledger(LEDGER_PATH).append([decision_entry(res, cid, decision, ss.get("approver") or "익명")])
    st.toast(f"{cid} 수정안을 {'승인' if decision == 'approved' else '반려'}했습니다. 판단 이력에 기록했습니다.")


# ---------------------------------------------------------------- 탭
tabs = st.tabs(["영향 지도", "수정안", "팀 간 충돌", "판단 이력", "실행 로그", "정답셋 평가"])

# 영향 지도 ---------------------------------------------------------
with tabs[0]:
    touched = [TOPICS[k].label for k in res["plan"]["priority"]]
    touched_txt = ", ".join(touched[:4]) + (f" 외 {len(touched) - 4}개" if len(touched) > 4 else "")
    st.markdown(f'<div class="rp-lead">{esc(event["name_ko"])} 공시가 건드린 지표 <b>{esc(touched_txt)}</b>를 '
                f'판단 연결 지도에서 따라가, 같은 회사·거래 관계·섹터 문서에서 흔들린 문장을 찾았습니다.</div>',
                unsafe_allow_html=True)
    groups: dict[str, list[str]] = {}
    for cid in res["impacted"]:
        groups.setdefault(verdicts[cid]["path"]["relation"], []).append(cid)
    blocks = []
    for rel in [r for r in REL_ORDER if r in groups]:
        cids = sorted(groups[rel], key=lambda c: (doc_order.index(claims[c]["doc_id"]), int(c[1:])))
        names = []
        for cid in cids:
            n = ctx.store.name_ko(claims[cid]["company"])
            if n not in names:
                names.append(n)
        sub = "섹터 전망 문서" if rel == "sector" else ", ".join(names)
        rows = "".join(
            f'<div class="row"><div>{vtag(verdicts[c]["verdict"])}</div>'
            f'<div class="txt">{esc(claims[c]["text"])}'
            + (f'<span class="cfb">팀 간 충돌</span>' if c in in_conflict else "") + "</div>"
            f'<div class="src"><b>{esc(docs_meta[claims[c]["doc_id"]].title)}</b><br>{esc(docs_meta[claims[c]["doc_id"]].team)}, {c}</div></div>'
            for c in cids)
        blocks.append(f'<div class="grp"><div class="grp-h"><div class="t">{REL_TITLE[rel]}<span>{esc(sub)}</span></div>'
                      f'<div class="n">{len(cids)}문장</div></div>{rows}</div>')
    st.markdown("".join(blocks), unsafe_allow_html=True)
    none_ids = [c for c in claims if verdicts[c]["verdict"] == "영향 없음"]
    st.markdown(f'<div class="rp-none">영향 없음 {len(none_ids)}문장({", ".join(none_ids)})은 이 공시와 이어진 경로가 없어 '
                '판정하지 않았습니다.</div>', unsafe_allow_html=True)

    st.markdown('<div class="sec-h">근거 보기</div><div class="sec-s">문장을 고르면 판정 이유와 공시 원문 근거를 보여 줍니다.</div>',
                unsafe_allow_html=True)
    pick = st.selectbox("근거를 볼 문장", ordered, index=0 if ordered else None, key="pick_map",
                        format_func=lambda c: f"[{verdicts[c]['verdict']}] {claims[c]['text']}  ({c})")
    if pick:
        left, right = st.columns([1, 1.15], gap="large")
        v = verdicts[pick]
        with left:
            st.markdown(claim_head(pick) + sentence_html(pick)
                        + f'<div class="reason">{esc(v["reason"])}</div>'
                        + f'<div class="meta">영향 경로: {esc(v["path"]["text"])}<br>탐색: {esc(trace_text(pick))}'
                        + f'<br>판정 규칙 {esc(v["rule"])}</div>', unsafe_allow_html=True)
        with right:
            st.markdown(evidence_block(pick), unsafe_allow_html=True)

# 수정안 ------------------------------------------------------------
with tabs[1]:
    c1, c2, c3 = st.columns([2.2, 1.2, 1.3], vertical_alignment="bottom")
    with c1:
        flt = st.segmented_control("보기", ["전체", "수정", "확인 필요", "유지"], default="수정", key="flt")
    with c2:
        st.text_input("승인자", value="데모 사용자", key="approver")
    with c3:
        st.download_button("수정안 내려받기 (.docx)", build_redline(res, ctx.docs, ss.decisions),
                           file_name=f"RedPen_수정안_{event['company']}_{res['run_id']}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           width="stretch")
    st.markdown('<div class="small" style="margin:4px 0 6px 0">수정안은 사실 전제만 고칩니다. 결론과 투자의견은 담당자가 '
                '판단하며, 원본 문서는 자동으로 바뀌지 않습니다. 내려받은 Word 파일에는 변경 내용 추적으로 표시됩니다.</div>',
                unsafe_allow_html=True)
    for cid in ordered:
        v = verdicts[cid]
        if flt and flt != "전체" and v["verdict"] != flt:
            continue
        n_ev = len(v["evidence_ids"])
        st.markdown(f'<div class="item">{claim_head(cid)}{sentence_html(cid)}'
                    f'<div class="reason">{esc(v["reason"])}</div>'
                    f'<details class="more"><summary>근거 {n_ev}건 보기</summary>{evidence_block(cid, show=99)}</details>'
                    f'</div>', unsafe_allow_html=True)
        if v["verdict"] == "수정":
            dec = ss.decisions.get(cid, "pending")
            b1, b2, b3 = st.columns([1, 1, 5])
            b1.button("승인", key=f"ok_{cid}", type="primary", disabled=dec != "pending",
                      on_click=record_decision, args=(cid, "approved"), width="stretch")
            b2.button("반려", key=f"no_{cid}", disabled=dec != "pending",
                      on_click=record_decision, args=(cid, "rejected"), width="stretch")
            state = {"pending": "검토 대기", "approved": "승인됨", "rejected": "반려됨"}[dec]
            b3.markdown(f'<div class="status">상태 <b>{state}</b></div>', unsafe_allow_html=True)

# 팀 간 충돌 --------------------------------------------------------
with tabs[2]:
    if not res["conflicts"]:
        st.markdown('<div class="rp-lead">이번 공시와 연결된 문장 가운데 서로 반대 방향인 문장 쌍이 없습니다.</div>',
                    unsafe_allow_html=True)
    else:
        st.markdown('<div class="rp-lead">같은 지표를 두고 팀끼리 반대 방향으로 판단한 문장입니다. RedPen은 어느 쪽이 맞는지 '
                    '정하지 않고, 최신 공시 근거가 어느 쪽에 더 많은지만 보여 줍니다.</div>', unsafe_allow_html=True)
    for g in res["conflicts"]:
        plus_l, minus_l = g["dir_labels"]
        st.markdown(f'<div class="item"><div class="cf-h">{esc(g["label"])}: ‘{esc(plus_l)}’ 대 ‘{esc(minus_l)}’</div>'
                    f'<div class="meta">{esc(", ".join(g["teams"]))}</div></div>', unsafe_allow_html=True)
        colp, colm = st.columns(2, gap="large")
        for col, side, lab in ((colp, g["plus"], plus_l), (colm, g["minus"], minus_l)):
            rows = "".join(
                f'<div class="side-row">{esc(claims[c]["text"])}<span class="who">{c}, '
                f'{esc(docs_meta[claims[c]["doc_id"]].title)}, {esc(docs_meta[claims[c]["doc_id"]].team)}, '
                f'판정 {esc(verdicts[c]["verdict"])}</span></div>' for c in side)
            col.markdown(f'<div class="side-h"><b>‘{esc(lab)}’</b>로 본 문장</div>{rows}', unsafe_allow_html=True)
        tot = max(1, g["evidence_plus"] + g["evidence_minus"])
        fav = {1: plus_l, -1: minus_l, 0: None}[g["favored"]]
        verdict_line = (f"최신 공시 근거는 ‘{fav}’ 쪽이 더 많습니다." if fav else "최신 공시 근거가 양쪽에 비슷하게 있습니다.")
        key_ev, seen = [], set()
        for c in g["plus"] + g["minus"]:
            for e in evidence.get(c, []):
                if e["kind"] == "text" and e["direction"] and e["sha256"] not in seen and e["stance"] != 0:
                    seen.add(e["sha256"])
                    key_ev.append(e)
        st.markdown(
            f'<div class="balance"><div style="width:{g["evidence_plus"] / tot * 100:.0f}%;background:var(--ink)"></div>'
            f'<div style="width:{g["evidence_minus"] / tot * 100:.0f}%;background:#B9C0CC"></div></div>'
            f'<div class="meta">최신 공시 근거: ‘{esc(plus_l)}’ {g["evidence_plus"]}건, ‘{esc(minus_l)}’ {g["evidence_minus"]}건 '
            f'(독립 근거 단위)</div><div class="reason">{esc(verdict_line)} 어느 판단을 따를지는 '
            f'{esc(", ".join(g["teams"]))} 담당자가 함께 정합니다.</div>'
            f'<details class="more"><summary>근거 문장 {len(key_ev)}건 보기</summary>'
            + "".join(evidence_html(e) for e in key_ev[:6]) + "</details>", unsafe_allow_html=True)

# 판단 이력 ---------------------------------------------------------
with tabs[3]:
    led = Ledger(LEDGER_PATH)
    rows = led.read()
    ok, n = led.verify_chain()
    st.markdown(f'<div class="rp-lead">판정·승인·반려를 시간 순서대로 쌓는 기록입니다. 항목마다 바로 앞 항목의 해시를 담아, '
                f'나중에 누가 고치면 드러납니다. 현재 <b>{len(rows)}건</b>, 해시 연결 '
                f'<b>{"정상" if ok else f"{n}번째 항목에서 끊김"}</b>.</div>', unsafe_allow_html=True)
    a, b = st.columns([3, 1], vertical_alignment="bottom")
    pick_id = a.selectbox("문장별 판단 경위", ["이번 실행 전체"] + sorted(res["impacted"]), key="led_pick")
    if rows:
        b.download_button("기록 내려받기 (.jsonl)", "\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                          file_name="redpen_ledger.jsonl", mime="application/json", width="stretch")
    view = ([r for r in rows if r.get("run_id") == res["run_id"]] if pick_id == "이번 실행 전체"
            else [r for r in rows if r.get("claim_id") == pick_id])
    type_ko = {"run": "실행", "judgment": "판정", "conflict": "충돌", "approval": "승인", "rejection": "반려"}
    df = pd.DataFrame([{
        "시각(UTC)": r["ts"].replace("T", " ")[:19], "유형": type_ko.get(r["type"], r["type"]),
        "문장": r.get("claim_id", ""), "판정": r.get("verdict", ""),
        "근거": str(len(r.get("evidence", []))) if r["type"] == "judgment" else "",
        "승인자": r.get("approver", ""),
        "내용": (r.get("revision") or r.get("reason") or r.get("event") or
               ", ".join("↔".join(p) for p in r.get("pairs", [])))[:80],
        "해시": r["hash"][:10]} for r in reversed(view)])
    st.dataframe(df, hide_index=True, width="stretch", height=min(520, 38 + 35 * max(1, len(df))))
    if pick_id != "이번 실행 전체":
        last = [r for r in view if r["type"] == "judgment"][-1:]
        if last:
            with st.expander("최근 판정의 근거 사본 (JSON)"):
                st.json(last[0]["evidence"], expanded=False)

# 실행 로그 ---------------------------------------------------------
with tabs[4]:
    seen_n, flow = {}, []
    for nd in res["node_path"]:
        seen_n[nd] = seen_n.get(nd, 0) + 1
        name = NODE_KO[nd].split(" (")[0][2:]
        again = seen_n[nd] > 1
        flow.append(f'<span class="nd{" again" if again else ""}">{esc(name)}{" (다시)" if again else ""}</span>')
    st.markdown(f'<div class="rp-lead">LangGraph로 만든 6단계가 실제로 지나간 순서입니다. 근거가 모자라면 탐색 단계로 돌아가고 '
                f'(<span style="color:var(--ochre)">다시</span>), 검증에 실패하면 판정 단계로 돌아갑니다.</div>'
                f'<div class="flow">' + '<span class="ar">→</span>'.join(flow) + "</div>"
                f'<div class="small" style="margin-bottom:14px">단계 {len(res["node_path"])}개, 체크포인트 {res["checkpoints"]}개, '
                f'{res["elapsed_s"]}초, LLM 호출 {res.get("llm_calls", 0)}회, 실행 ID {esc(res["run_id"])}</div>',
                unsafe_allow_html=True)
    def log_rows(items):
        return "".join(
            f'<div class="log"><span class="tm">{esc(l["t"])}</span>'
            f'<span class="nm">{esc(NODE_KO[l["node"]].split(" (")[0][2:])}</span><span>{esc(l["msg"])}</span></div>'
            for l in items)

    key = [l for l in res["log"] if l["node"] in ("planner", "verifier_writer")
           or (l["node"] == "impact_mapper" and l["msg"].startswith("판단 연결 지도"))
           or (l["node"] == "retriever" and l.get("attempt", 1) > 1)
           or (l["node"] == "judge" and ("재탐색:" in l["msg"] or l["msg"].startswith("충돌")))]
    key = [l for i, l in enumerate(key) if l["msg"] not in {k["msg"] for k in key[:i]}]  # 같은 충돌 줄은 한 번만
    st.markdown('<div class="sec-h">주요 결정</div><div class="sec-s">계획, 재탐색 결정과 범위 확장, 충돌 발견, 검증 결과만 '
                '골랐습니다.</div>' + log_rows(key), unsafe_allow_html=True)
    with st.expander(f"전체 실행 로그 {len(res['log'])}줄"):
        st.markdown(log_rows(res["log"]), unsafe_allow_html=True)
    with st.expander("판단 연결 지도 그래프 (Graphviz)"):
        st.graphviz_chart(ctx.jmap.to_dot(event, verdicts, VC, res["conflicts"]), width="stretch")

# 정답셋 평가 -------------------------------------------------------
with tabs[5]:
    ev = evaluate(res, gold)
    if not ev:
        st.markdown('<div class="rp-lead">이 공시에는 아직 정답셋 라벨이 없습니다. 지금은 마이크론 10-K와 AMD 10-Q에만 '
                    '라벨이 있습니다.</div>', unsafe_allow_html=True)
    else:
        pct = lambda x: "-" if x is None else f"{x * 100:.0f}%"  # noqa: E731
        m = [("영향 문장 탐지율", pct(ev["impact_recall"]), "정답셋의 영향 문장 중 찾은 비율"),
             ("영향 판정 정밀도", pct(ev["impact_precision"]), "찾은 문장 중 실제 영향 비율"),
             ("영향 없음 오탐률", pct(ev["no_impact_fp_rate"]), "영향 없는 문장을 잘못 고른 비율"),
             ("판정 일치율", pct(ev["verdict_accuracy"]), f"{ev['n_sentences']}문장, 4가지 판정"),
             ("충돌 탐지", pct(ev["conflict_recall"]), f"정밀도 {pct(ev['conflict_precision'])}"),
             ("수치·출처 오류", f"{ev['numeric_errors']}건", "검증 단계 재대조 결과")]
        st.markdown('<div class="metric-grid">' + "".join(
            f'<div><div class="l">{l}</div><div class="v">{v}</div><div class="s">{s}</div></div>' for l, v, s in m)
            + "</div>", unsafe_allow_html=True)
        cm = pd.DataFrame(ev["confusion"]).T
        cm.index.name = "정답 \\ 예측"
        st.markdown('<div class="sec-h">판정 혼동 행렬</div>', unsafe_allow_html=True)
        st.dataframe(cm, width="stretch")
        if ev["mismatches"]:
            st.markdown('<div class="sec-h">정답과 다른 문장</div>', unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(ev["mismatches"]), hide_index=True, width="stretch")
        st.markdown('<div class="small" style="margin-top:14px; line-height:1.7">이 수치는 일반화 성능이 아닙니다. 정답셋은 데모 '
                    '문서를 쓴 팀원이 만든 초안이고 판정 규칙도 같은 문서로 다듬었으므로, 설계한 대로 동작하는지 확인하는 '
                    '용도입니다. 본선에서는 다른 팀원이 다시 라벨링하고 문서와 공시를 늘려 단일 LLM 프롬프트와 비교합니다.</div>',
                    unsafe_allow_html=True)
