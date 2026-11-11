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

VC = {  # 판정 색: (선, 배경)
    "수정": ("#C62828", "#FDECEA"), "확인 필요": ("#C55A11", "#FFF1E6"), "유지": ("#2E7D32", "#E9F5EA"),
    "영향 없음": ("#9AA3B2", "#F3F5F8"), "충돌": ("#6A1B9A", "#F3E8F8"),
}
STANCE_KO = {1: "같은 방향", -1: "반대", 0: "참고"}

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
html, body, [class*="css"], .stMarkdown, button, input, textarea {
  font-family: "Pretendard", "Noto Sans KR", "Noto Sans CJK KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
}
.block-container {padding-top: 3.2rem; padding-bottom: 3rem; max-width: 1400px;}
header[data-testid="stHeader"] {background: transparent;}
.rp-head {display:flex; align-items:flex-end; gap:14px; margin-bottom:4px;}
.rp-logo {font-size:30px; font-weight:800; color:#0B2545; letter-spacing:-0.5px;}
.rp-logo span {color:#C62828;}
.rp-sub {font-size:15px; color:#3A4556; padding-bottom:5px;}
.rp-badges {display:flex; flex-wrap:wrap; gap:6px; margin:6px 0 14px 0;}
.rp-badge {font-size:12px; padding:3px 9px; border-radius:999px; background:#EEF2F7; color:#3A4556; border:1px solid #DCE3EC;}
.rp-badge.real {background:#E8F1FB; color:#0B4F8A; border-color:#C5DBF2;}
.rp-badge.fake {background:#FFF6E5; color:#8A5A00; border-color:#F2DDB0;}
.rp-event {border:1px solid #D5DDE8; border-left:5px solid #0B2545; border-radius:10px; padding:12px 16px; background:#FBFCFE; margin-bottom:12px;}
.rp-event .t {font-size:12px; color:#59657A; font-weight:600; letter-spacing:0.3px;}
.rp-event .h {font-size:18px; font-weight:700; color:#0B2545; margin:2px 0 4px 0;}
.rp-event .m {font-size:13px; color:#3A4556;}
.rp-event a {color:#0070C0; text-decoration:none;}
.rp-kpis {display:grid; grid-template-columns: repeat(6, 1fr); gap:10px; margin: 4px 0 14px 0;}
.rp-kpi {border:1px solid #E1E6EE; border-radius:10px; padding:10px 12px; background:#fff;}
.rp-kpi .l {font-size:12px; color:#59657A;}
.rp-kpi .v {font-size:24px; font-weight:800; color:#0B2545; line-height:1.25;}
.rp-kpi .s {font-size:11.5px; color:#7A8597;}
.chip {display:inline-block; font-size:12px; font-weight:700; padding:2px 8px; border-radius:6px; margin-right:6px; vertical-align:1px;}
.sid {display:inline-block; font-size:11.5px; font-weight:700; color:#59657A; background:#EEF2F7; border-radius:5px; padding:1px 6px; margin-right:6px;}
.doc-card {border:1px solid #E1E6EE; border-radius:10px; padding:12px 14px; background:#fff; margin-bottom:10px;}
.doc-card .dt {font-weight:700; color:#0B2545; font-size:15px;}
.doc-card .dm {font-size:12px; color:#7A8597; margin-bottom:6px;}
.sent {padding:5px 8px; border-left:4px solid #E1E6EE; margin:4px 0; border-radius:0 6px 6px 0; font-size:14px; color:#1F2733;}
.sent.none {color:#8A94A6; border-left-color:#EEF1F5;}
.redline {font-size:15px; line-height:1.7; padding:8px 12px; background:#FFFDFC; border:1px dashed #E8B4B0; border-radius:8px;}
.redline del {color:#9A2B2B; text-decoration-thickness:1.5px;}
.redline ins {color:#C62828; text-decoration: underline; text-decoration-color:#C62828; font-weight:600;}
.reason {font-size:13.5px; color:#3A4556; margin:6px 0;}
.path {font-size:12.5px; color:#59657A;}
.ev {border:1px solid #E1E6EE; border-radius:8px; padding:8px 11px; margin:6px 0; background:#FCFDFF;}
.ev .hd {font-size:12px; color:#59657A; margin-bottom:3px;}
.ev .q {font-size:13.5px; color:#1F2733; font-family: "Source Serif 4", Georgia, serif;}
.ev .fm {font-size:12.5px; color:#3A4556; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;}
.ev .ft {font-size:11.5px; color:#8A94A6; margin-top:3px;}
.ev .ft a {color:#0070C0; text-decoration:none;}
.tag {display:inline-block; font-size:11px; font-weight:700; padding:1px 6px; border-radius:4px; margin-right:5px;}
.tag.fact {background:#E8F1FB; color:#0B4F8A;} .tag.calc {background:#EAF5EA; color:#1E6B23;} .tag.hyp {background:#FFF1E6; color:#9A4A0B;}
.st {display:inline-block; font-size:11px; font-weight:700; padding:1px 6px; border-radius:4px; margin-right:6px;}
.st.p {background:#E9F5EA; color:#2E7D32;} .st.n {background:#FDECEA; color:#C62828;} .st.z {background:#EEF1F5; color:#59657A;}
.trace {font-size:12px; color:#59657A; margin-top:4px;}
.kside {border:1px solid #E1E6EE; border-radius:10px; padding:10px 12px; background:#fff; height:100%;}
.kside .k {font-weight:700; font-size:14px; margin-bottom:6px;}
.bar {display:flex; height:10px; border-radius:6px; overflow:hidden; background:#EEF1F5; margin:6px 0 4px 0;}
.log {font-size:13px; padding:4px 0; border-bottom:1px solid #F0F2F6;}
.log .n {display:inline-block; min-width:170px; font-weight:700; color:#0B2545;}
.log .tm {color:#9AA3B2; font-size:11.5px; margin-right:8px;}
.flow {display:flex; flex-wrap:wrap; align-items:center; gap:4px; margin:6px 0 12px 0;}
.flow .node {font-size:12px; padding:4px 8px; border-radius:6px; background:#0B2545; color:#fff;}
.flow .node.loop {background:#C55A11;}
.flow .arr {color:#9AA3B2; font-size:12px;}
.legend span {margin-right:12px; font-size:12.5px; color:#3A4556;}
.small {font-size:12.5px; color:#59657A;}
</style>
""", unsafe_allow_html=True)


def esc(x) -> str:
    return html.escape(str(x))


def chip(verdict: str) -> str:
    line, bg = VC[verdict]
    return f'<span class="chip" style="color:{line};background:{bg};border:1px solid {line}33">{esc(verdict)}</span>'


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
    st.markdown('<div class="rp-logo">Red<span>Pen</span></div>', unsafe_allow_html=True)
    st.caption("판단 빨간펜 AI Agent · MVP")
    ctx_rules = get_context("rules")
    events = ctx_rules.store.events()
    default_idx = next((i for i, e in enumerate(events) if e["company"] == DEFAULT_EVENT_COMPANY), 0)
    ev_idx = st.radio("새 공시 선택", list(range(len(events))), index=default_idx,
                      format_func=lambda i: f"{events[i]['name_ko']}({events[i]['role']}) · "
                                            f"{events[i]['members'][0]['form']} {events[i]['filed']}")
    engine_opt = st.radio("판정 엔진", ["rules", "llm"], index=0,
                          format_func=lambda x: "규칙 기반 (API 키 불필요)" if x == "rules" else "LLM 보조 (키 필요)",
                          captions=None)
    if engine_opt == "llm" and not llm_available:
        st.info("LLM 키가 설정되지 않아 규칙 기반으로 실행합니다. `REDPEN_LLM_PROVIDER`와 API 키를 설정하면 "
                "주장 추출·근거 방향 분류·수정 문안에 LLM을 씁니다 (수치는 항상 코드 계산).")
        engine_opt = "rules"
    passcode = os.getenv("REDPEN_LLM_PASSCODE", "")
    if engine_opt == "llm" and passcode:  # 공개 데모에서 API 비용 보호
        code = st.text_input("LLM 모드 접근 코드", type="password", key="llm_code")
        if not hmac.compare_digest(code.encode(), passcode.encode()):
            st.caption("접근 코드를 입력해야 LLM 모드가 켜집니다. 그 전에는 규칙 기반으로 실행합니다.")
            engine_opt = "rules"
    run_clicked = st.button("▶  Agent 실행", type="primary", width="stretch")
    approver = st.text_input("승인자 이름 (판단 이력 원장 기록)", value="데모 사용자")
    st.divider()
    st.markdown('<div class="small"><b>데이터</b><br>공시·XBRL: SEC EDGAR 실데이터 스냅샷 (2026-11-11 수집)'
                '<br>내부 문서: 팀이 작성한 <b>가상 문서</b> 4부 (실제 KIC 자료 아님)</div>', unsafe_allow_html=True)
    with st.expander("실시간 새 공시 확인 (EDGAR)"):
        st.caption("SEC_USER_AGENT 환경 변수(이름·연락처)가 있을 때만 SEC API를 호출합니다 (초당 10회 이하).")
        if st.button("지금 확인", key="live_check", width="stretch"):
            try:
                from redpen.edgar import EdgarClient, check_new_filings
                new = check_new_filings(ctx_rules.store, EdgarClient(cache_dir=ROOT / ".cache" / "edgar"))
                if new:
                    for n in new:
                        st.markdown(f"- {n['company']} {n['form']} {n['filingDate']} (`{n['accessionNumber']}`)")
                    st.caption("새 공시를 반영하려면 scripts/build_snapshot.py로 스냅샷을 다시 만듭니다.")
                else:
                    st.success("스냅샷 이후 새 실적 공시가 없습니다.")
            except Exception as e:  # noqa: BLE001
                st.warning(f"확인할 수 없습니다: {type(e).__name__}: {e}")

ctx = get_context(engine_opt)
event = events[ev_idx]
gold = get_gold()


def do_run():
    with st.sidebar, st.status("RedPen Agent 실행 중…", expanded=False) as status:
        def on_step(node, update):
            status.update(label=f"{NODE_KO[node]} 완료")
        res = run(ctx, event["id"], on_step=on_step, record=True)
        status.update(label=f"완료 — {len(res['node_path'])}단계 · {res['elapsed_s']}초", state="complete")
    ss.result = res
    ss.decisions = {}


if run_clicked or ss.result is None or ss.result["event"]["id"] != event["id"] or ss.result["engine"] != engine_opt:
    do_run()
res = ss.result
verdicts, claims, evidence = res["verdicts"], res["claims"], res["evidence"]
docs_meta = {d.id: d for d in ctx.docs}
counts = {k: sum(1 for v in verdicts.values() if v["verdict"] == k) for k in ("수정", "확인 필요", "유지", "영향 없음")}

# ---------------------------------------------------------------- 헤더
st.markdown(f"""
<div class="rp-head"><div class="rp-logo">Red<span>Pen</span></div>
<div class="rp-sub">새 공시 1건이 흔드는 기관 내 판단을 찾아, 근거와 함께 고칩니다</div></div>
<div class="rp-badges">
<span class="rp-badge real">공시·XBRL: SEC EDGAR 실데이터 (2026-11-11 스냅샷)</span>
<span class="rp-badge fake">내부 문서: 가상 문서 4부 (데모용)</span>
<span class="rp-badge">엔진: {esc(res['engine_label'])}</span>
<span class="rp-badge">LangGraph 6단계 · 체크포인트 {res['checkpoints']}개</span>
</div>""", unsafe_allow_html=True)

members = " + ".join(
    f'<a href="{esc(m["url"])}" target="_blank">{esc(m["form"])} {esc(m["filed"])}</a>'
    + ("" if m["in_snapshot"] else " (본문 미수집)") for m in event["members"])
touched = res["plan"]["touched"]
st.markdown(f"""
<div class="rp-event"><div class="t">① 새 공시 감지</div>
<div class="h">{esc(event['name_ko'])}({esc(event['role'])}) · {esc(event['fiscal'])} 실적 공시</div>
<div class="m">묶음 공시 {members} · 점검 지표: {esc(' → '.join(TOPICS[k].label for k in res['plan']['priority']))}</div>
</div>""", unsafe_allow_html=True)

n_imp = len(res["impacted"])
n_re = sum(1 for a in res["attempts"].values() if a > 1)
vf = res["verification"]
kpis = [
    ("영향 문장", f"{n_imp}<span style='font-size:14px;color:#7A8597'> / {len(claims)}</span>",
     f"문서 {len({claims[c]['doc_id'] for c in res['impacted']})}부 · 다른 팀 문서 포함"),
    ("수정", f"<span style='color:{VC['수정'][0]}'>{counts['수정']}</span>", "독립 근거 2건 이상"),
    ("확인 필요", f"<span style='color:{VC['확인 필요'][0]}'>{counts['확인 필요']}</span>", "사람이 최종 판단"),
    ("유지 (근거 강화)", f"<span style='color:{VC['유지'][0]}'>{counts['유지']}</span>", "공시가 같은 방향"),
    ("팀 간 충돌", f"<span style='color:{VC['충돌'][0]}'>{len(res['conflicts'])}</span>",
     f"상충 문장 쌍 {sum(len(g['pairs']) for g in res['conflicts'])}개"),
    ("검증", "통과" if vf["passed"] else "실패",
     f"인용 {vf['quotes_checked']} · 수치 {vf['numbers_checked']} · 불일치 {len(vf['failures'])}건"),
]
st.markdown('<div class="rp-kpis">' + "".join(
    f'<div class="rp-kpi"><div class="l">{l}</div><div class="v">{v}</div><div class="s">{s}</div></div>'
    for l, v, s in kpis) + "</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------- 공통 렌더러
def evidence_html(e: dict) -> str:
    lab = label3(e)
    cls = {"사실": "fact", "계산값": "calc", "가설": "hyp"}[lab]
    stc = {1: "p", -1: "n", 0: "z"}[e["stance"]]
    head = (f'<span class="tag {cls}">{lab}</span><span class="st {stc}">{STANCE_KO[e["stance"]]}</span>'
            f'<b>{esc(e["id"])}</b> · {esc(e["company"])} {esc(e["location"])}')
    if e.get("metric"):
        m = e["metric"]
        body = (f'<div class="q"><b>{esc(e["label"])} {esc(m.get("period", ""))}</b> — {esc(e["basis"])}</div>'
                f'<div class="fm">{esc(m["formula"])}</div>')
        if m.get("prev_formula"):
            body += f'<div class="fm">전년 동기 {esc(m["prev_period"])}: {esc(m["prev_formula"])} = {m["prev_value"]}%</div>'
        if m.get("derived"):
            body += '<div class="fm">4분기 = 10-K 연간값 − 3분기 누계 (계산식 기록)</div>'
    else:
        body = f'<div class="q">“{esc(e["quote"])}”</div>'
    found = "수치 계산" if e["attempt"] == 0 else f"{e['attempt']}차 탐색"
    foot = (f'{esc(e["form"])} 접수 {esc(e["filed"])} · 접수번호 {esc(e["accession"])} · SHA-256 {esc(e["sha256"][:12])}… '
            f'· {found} · <a href="{esc(e["url"])}" target="_blank">SEC 원문</a>')
    return f'<div class="ev"><div class="hd">{head}</div>{body}<div class="ft">{foot}</div></div>'


def claim_header(cid: str) -> str:
    c, v = claims[cid], verdicts[cid]
    d = docs_meta[c["doc_id"]]
    mod = "가설(전망)" if c["modality"] == "forecast" else "사실 전제"
    topic = TOPICS[c["topic"]].label if c["topic"] else "-"
    return (f'{chip(v["verdict"])}<span class="sid">{cid}</span><b>{esc(d.title)}</b> '
            f'<span class="small">· {esc(d.team)} · {esc(topic)} · {mod} · 규칙 {esc(v["rule"])}</span>')


def claim_body(cid: str, show_all_ev: bool = True) -> str:
    c, v = claims[cid], verdicts[cid]
    if v["verdict"] == "수정" and v.get("revision"):
        dec = ss.decisions.get(cid, "pending")
        txt = (f'<div class="redline"><del>{esc(c["text"])}</del><br><ins>{esc(v["revision"])}</ins></div>'
               if dec != "rejected" else f'<div class="redline">{esc(c["text"])} <span class="small">(수정안 반려됨)</span></div>')
    else:
        txt = f'<div class="redline" style="border-color:#E1E6EE;background:#fff">{esc(c["text"])}</div>'
    out = [txt, f'<div class="reason">{esc(v["reason"])}</div>']
    if v.get("path"):
        out.append(f'<div class="path">영향 경로: {esc(v["path"]["text"])}</div>')
    evs = [e for e in evidence.get(cid, []) if e["id"] in v["evidence_ids"]]
    if not show_all_ev:
        evs = [e for e in evs if e["stance"] != 0] or evs[:2]
    out += [evidence_html(e) for e in evs]
    tr = res["traces"].get(cid, [])
    if tr:
        steps = []
        for t in tr:
            chg = []
            if t.get("added_terms"):
                chg.append("검색어 +" + ", ".join(t["added_terms"]))
            if t.get("added_corpus"):
                chg.append("범위 +" + ", ".join(t["added_corpus"]))
            steps.append(f'{t["attempt"]}차 ' + (" · ".join(chg) if chg else f'공시 {len(t["corpus"])}건') + f' → {t["new"]}건')
        note = res["calc_notes"].get(cid, "")
        out.append(f'<div class="trace">탐색 이력: {esc(" ⟶ ".join(steps))}' + (f' · 수치: {esc(note)}' if note else "") + "</div>")
    return "".join(out)


def record_decision(cid: str, decision: str):
    ss.decisions[cid] = decision
    Ledger(LEDGER_PATH).append([decision_entry(res, cid, decision, approver or "익명")])
    st.toast(f"{cid} 수정안 {'승인' if decision == 'approved' else '반려'} — 판단 이력 원장에 기록했습니다.")


# ---------------------------------------------------------------- 탭
tabs = st.tabs(["① 영향 문장 지도", "② 판정·빨간펜 수정안", "③ 팀 간 충돌 리포트", "④ 판단 이력 원장",
                "⑤ Agent 실행 로그", "⑥ 정답셋 평가"])

# ① 영향 문장 지도 ------------------------------------------------
with tabs[0]:
    st.markdown('<div class="legend">' + "".join(
        f'<span>{chip(k)}</span>' for k in ("수정", "확인 필요", "유지")) +
        '<span style="color:#9AA3B2">회색 = 영향 없음</span>'
        '<span style="color:#6A1B9A">⇄ 보라 점선 = 팀 간 충돌 문장 쌍</span>'
        '<span class="small">판단 연결 지도(문장–지표–기업–문서)에서 새 공시 회사와 관계(같은 회사·공급사·경쟁사·섹터)로 '
        '이어진 문장만 판정합니다.</span></div>', unsafe_allow_html=True)
    st.graphviz_chart(ctx.jmap.to_dot(event, verdicts, VC, res["conflicts"]), width="stretch")
    st.markdown("##### 문장 선택 → 근거 카드")
    order = sorted(res["impacted"], key=lambda c: (["수정", "확인 필요", "유지"].index(verdicts[c]["verdict"]), c))
    pick = st.pills("영향 문장", order, default=order[0] if order else None,
                    format_func=lambda c: f"{c} · {verdicts[c]['verdict']}", label_visibility="collapsed",
                    key="pick_map")
    if pick:
        with st.container(border=True):
            st.markdown(claim_header(pick), unsafe_allow_html=True)
            st.markdown(claim_body(pick), unsafe_allow_html=True)

# ② 판정·빨간펜 수정안 ---------------------------------------------
with tabs[1]:
    c1, c2 = st.columns([3, 1.2])
    with c1:
        flt = st.segmented_control("판정 필터", ["전체", "수정", "확인 필요", "유지"], default="전체",
                                   label_visibility="collapsed", key="flt")
    with c2:
        st.download_button("빨간펜 수정안 (변경 추적 .docx)", build_redline(res, ctx.docs, ss.decisions),
                           file_name=f"RedPen_수정안_{event['company']}_{res['run_id']}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           width="stretch", icon=":material/download:")
    st.caption("수정안은 사실 전제만 고치며 결론·투자의견은 담당자가 판단합니다. 원본은 자동으로 덮어쓰지 않고, "
               "승인·반려는 판단 이력 원장에 기록됩니다.")
    order = sorted(res["impacted"], key=lambda c: (["수정", "확인 필요", "유지"].index(verdicts[c]["verdict"]), c))
    for cid in order:
        v = verdicts[cid]
        if flt and flt != "전체" and v["verdict"] != flt:
            continue
        with st.container(border=True):
            st.markdown(claim_header(cid), unsafe_allow_html=True)
            st.markdown(claim_body(cid), unsafe_allow_html=True)
            if v["verdict"] == "수정":
                dec = ss.decisions.get(cid, "pending")
                b1, b2, b3 = st.columns([1, 1, 4])
                b1.button("승인", key=f"ok_{cid}", type="primary", disabled=dec != "pending",
                          on_click=record_decision, args=(cid, "approved"), width="stretch")
                b2.button("반려", key=f"no_{cid}", disabled=dec != "pending",
                          on_click=record_decision, args=(cid, "rejected"), width="stretch")
                b3.markdown(f'<div class="small" style="padding-top:8px">상태: '
                            f'<b>{ {"pending": "검토 대기", "approved": "승인됨", "rejected": "반려됨"}[dec] }</b>'
                            f'{" · 승인자 " + esc(approver) if dec != "pending" else ""}</div>', unsafe_allow_html=True)

# ③ 팀 간 충돌 리포트 ----------------------------------------------
with tabs[2]:
    if not res["conflicts"]:
        st.info("이번 공시와 연결된 문장 가운데 서로 반대 방향인 문장 쌍이 없습니다.")
    for g in res["conflicts"]:
        with st.container(border=True):
            fav = {1: g["dir_labels"][0], -1: g["dir_labels"][1], 0: "판단 보류"}[g["favored"]]
            st.markdown(f'{chip("충돌")}<b style="font-size:16px">{g["id"]} · {esc(g["label"])}</b> '
                        f'<span class="small">· {esc(" · ".join(g["teams"]))} · 상충 문장 쌍 '
                        f'{esc(", ".join(" ↔ ".join(p) for p in g["pairs"]))}</span>', unsafe_allow_html=True)
            colp, colm = st.columns(2)
            for col, side, lab, color in ((colp, g["plus"], g["dir_labels"][0], "#0070C0"),
                                          (colm, g["minus"], g["dir_labels"][1], "#C55A11")):
                items = "".join(
                    f'<div class="sent" style="border-left-color:{color}"><span class="sid">{cid}</span>'
                    f'{esc(claims[cid]["text"])}<br><span class="small">{esc(docs_meta[claims[cid]["doc_id"]].title)} · '
                    f'{esc(docs_meta[claims[cid]["doc_id"]].team)} · {esc(docs_meta[claims[cid]["doc_id"]].date)} · '
                    f'판정 {esc(verdicts[cid]["verdict"])}</span></div>' for cid in side)
                col.markdown(f'<div class="kside"><div class="k" style="color:{color}">‘{esc(lab)}’ 쪽 판단</div>'
                             f'{items}</div>', unsafe_allow_html=True)
            tot = max(1, g["evidence_plus"] + g["evidence_minus"])
            st.markdown(
                f'<div class="small" style="margin-top:8px">최신 공시 근거 (독립 근거 단위): '
                f'‘{esc(g["dir_labels"][0])}’ {g["evidence_plus"]}건 · ‘{esc(g["dir_labels"][1])}’ {g["evidence_minus"]}건</div>'
                f'<div class="bar"><div style="width:{g["evidence_plus"] / tot * 100:.0f}%;background:#0070C0"></div>'
                f'<div style="width:{g["evidence_minus"] / tot * 100:.0f}%;background:#C55A11"></div></div>'
                f'<div class="reason">최신 공시는 <b>‘{esc(fav)}’</b> 쪽 근거가 많습니다. 어느 판단을 따를지는 양쪽 담당자'
                f'({esc(" · ".join(g["teams"]))})가 결정합니다.</div>', unsafe_allow_html=True)
            key_ev, seen = [], set()
            for cid in g["plus"] + g["minus"]:
                for e in evidence.get(cid, []):
                    if e["direction"] and e["kind"] == "text" and e["sha256"] not in seen and e["stance"] != 0:
                        seen.add(e["sha256"])
                        key_ev.append(e)
            with st.expander(f"근거 문장 {len(key_ev)}건 보기"):
                st.markdown("".join(evidence_html(e) for e in key_ev[:6]), unsafe_allow_html=True)

# ④ 판단 이력 원장 --------------------------------------------------
with tabs[3]:
    led = Ledger(LEDGER_PATH)
    rows = led.read()
    ok, n = led.verify_chain()
    a, b = st.columns([3, 1])
    a.markdown(f'<div class="reason">append-only 원장 · 항목마다 직전 항목의 해시를 포함해 사후 변경을 탐지합니다. '
               f'해시 체인: <b style="color:{"#2E7D32" if ok else "#C62828"}">{"정상" if ok else f"{n}번째 항목에서 불일치"}</b> '
               f'· 전체 {len(rows)}건</div>', unsafe_allow_html=True)
    if rows:
        b.download_button("원장 내려받기 (.jsonl)", "\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                          file_name="redpen_ledger.jsonl", mime="application/json", width="stretch")
    pick_id = st.selectbox("판단 경위 재구성: 문장 선택", ["(이번 실행 전체)"] + sorted(res["impacted"]), key="led_pick")
    if pick_id == "(이번 실행 전체)":
        view = [r for r in rows if r.get("run_id") == res["run_id"]]
    else:
        view = [r for r in rows if r.get("claim_id") == pick_id]
    type_ko = {"run": "실행", "judgment": "판정", "conflict": "충돌", "approval": "승인", "rejection": "반려"}
    df = pd.DataFrame([{
        "시각(UTC)": r["ts"].replace("T", " ")[:19], "유형": type_ko.get(r["type"], r["type"]),
        "실행": r["run_id"][-6:], "문장": r.get("claim_id", ""), "판정": r.get("verdict", ""),
        "규칙": r.get("rule", ""), "근거 수": str(len(r.get("evidence", []))) if r["type"] == "judgment" else "",
        "승인자": r.get("approver", ""), "내용": (r.get("revision") or r.get("reason") or r.get("event") or
                                              ", ".join("↔".join(p) for p in r.get("pairs", [])))[:90],
        "해시": r["hash"][:10] + "…"} for r in reversed(view)])
    st.dataframe(df, hide_index=True, width="stretch", height=min(560, 38 + 35 * max(1, len(df))))
    if pick_id != "(이번 실행 전체)" and view:
        last = [r for r in view if r["type"] == "judgment"][-1:]
        if last:
            with st.expander("가장 최근 판정의 근거 스냅샷 (JSON)"):
                st.json(last[0]["evidence"], expanded=False)

# ⑤ Agent 실행 로그 -------------------------------------------------
with tabs[4]:
    path = res["node_path"]
    seen_n, flow = {}, []
    for nd in path:
        seen_n[nd] = seen_n.get(nd, 0) + 1
        loop = seen_n[nd] > 1
        flow.append(f'<span class="node{" loop" if loop else ""}">{esc(NODE_KO[nd].split(" (")[0])}'
                    f'{" ↺" + str(seen_n[nd]) if loop else ""}</span>')
    st.markdown('<div class="small">실행 경로 (LangGraph 상태 그래프, 주황 = 재탐색·재판정 루프)</div>'
                '<div class="flow">' + '<span class="arr">→</span>'.join(flow) + "</div>", unsafe_allow_html=True)
    st.markdown(f'<div class="small">단계 {len(path)}개 · 체크포인트 {res["checkpoints"]}개 · 실행 {res["elapsed_s"]}초 · '
                f'재탐색 문장 {n_re}개 · LLM 호출 {res.get("llm_calls", 0)}회 · 실행 ID {esc(res["run_id"])}</div>',
                unsafe_allow_html=True)
    st.markdown("".join(
        f'<div class="log"><span class="tm">{esc(l["t"])}</span><span class="n">{esc(NODE_KO[l["node"]])}</span>'
        f'{esc(l["msg"])}</div>' for l in res["log"]), unsafe_allow_html=True)

# ⑥ 정답셋 평가 -----------------------------------------------------
with tabs[5]:
    ev = evaluate(res, gold)
    if not ev:
        st.info("이 공시에는 아직 정답셋 라벨이 없습니다. (현재 라벨: 마이크론 10-K, AMD 10-Q)")
    else:
        pct = lambda x: "-" if x is None else f"{x * 100:.0f}%"  # noqa: E731
        m = [("영향 문장 탐지율", pct(ev["impact_recall"]), "정답셋 영향 문장 중 찾은 비율"),
             ("영향 판정 정밀도", pct(ev["impact_precision"]), "찾은 문장 중 실제 영향"),
             ("무영향 문장 오탐률", pct(ev["no_impact_fp_rate"]), "영향 없음을 영향으로 본 비율"),
             ("판정 일치율", pct(ev["verdict_accuracy"]), f"{ev['n_sentences']}문장 · 4분류"),
             ("충돌 탐지", pct(ev["conflict_recall"]), f"정밀도 {pct(ev['conflict_precision'])}"),
             ("수치·출처 오류", f"{ev['numeric_errors']}건", "검증 단계 재대조")]
        st.markdown('<div class="rp-kpis">' + "".join(
            f'<div class="rp-kpi"><div class="l">{l}</div><div class="v">{v}</div><div class="s">{s}</div></div>'
            for l, v, s in m) + "</div>", unsafe_allow_html=True)
        cm = pd.DataFrame(ev["confusion"]).T
        cm.index.name = "정답 \\ 예측"
        st.markdown("##### 판정 혼동 행렬")
        st.dataframe(cm, width="stretch")
        if ev["mismatches"]:
            st.markdown("##### 불일치 문장")
            st.dataframe(pd.DataFrame(ev["mismatches"]), hide_index=True, width="stretch")
        st.caption("주의: 정답셋은 데모 문서를 쓴 팀원이 만든 1차 라벨(초안)이고, 판정 규칙도 같은 문서로 다듬었습니다. "
                   "따라서 이 수치는 '설계한 대로 동작한다'는 확인이지 일반화 성능이 아닙니다. 기획서대로 본선에서는 작성자와 "
                   "다른 팀원이 다시 라벨링하고 문서·공시를 늘려, 같은 입력을 넣은 단일 LLM 프롬프트와 비교합니다.")
