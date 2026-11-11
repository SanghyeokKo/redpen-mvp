"""RedPen Agent 워크플로 — LangGraph 상태 그래프.

  ① Planner → ② Impact Mapper → ③ Retriever → ④ Calculator → ⑤ Judge → ⑥ Verifier·Writer
                                      ↑______ 근거 부족: 검색어·범위 바꿔 재탐색 (최대 3회) ___|
                                                            ↑___ 검증 실패: 재판정 ___|
"""
from __future__ import annotations

import operator
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .claims import extract_all, load_docs
from .judgment_map import JudgmentMap, touched_topics
from .outputs import Ledger, run_entries
from .retrieval import guidance_evidence, retrieve
from .rules import MAX_ATTEMPTS, calc_evidence, detect_conflicts, judge_claim, register_ciks, revision_text
from .store import Store
from .topics import TOPICS
from .verify import verify

NODE_KO = {"planner": "① 공시 감지·계획 (Planner)", "impact_mapper": "② 영향 범위 탐색 (Impact Mapper)",
           "retriever": "③ 근거 탐색 (Retriever)", "calculator": "④ 수치 확인 (Calculator)",
           "judge": "⑤ 판정·충돌 검사 (Judge)", "verifier_writer": "⑥ 검증·기록 (Verifier·Writer)"}
MAX_VERIFY_ROUNDS = 2


class RunState(TypedDict, total=False):
    run_id: str
    event_id: str
    engine: str
    engine_label: str
    started: float
    event: dict
    plan: dict
    claims: dict
    impacted: list
    paths: dict
    pending: list
    attempts: dict
    evidence: dict
    traces: dict
    calc_notes: dict
    calculated: list
    verdicts: dict
    conflicts: list
    verification: dict
    verify_rounds: int
    excluded: list
    unfinished: list
    llm_calls: int
    log: Annotated[list, operator.add]


@dataclass
class Context:
    store: Store
    docs: list
    claims: dict
    jmap: JudgmentMap
    llm: Any = None
    budget_s: float = 300.0
    ledger_path: Path | None = None

    @property
    def docs_meta(self) -> dict:
        return {d.id: {"team": d.team, "title": d.title, "date": d.date, "author": d.author} for d in self.docs}


def load_context(data_dir: str | Path, llm=None, ledger_path: str | Path | None = None,
                 snapshot_dir: str | Path | None = None) -> Context:
    data_dir = Path(data_dir)
    store = Store(data_dir, snapshot_dir or os.getenv("REDPEN_SNAPSHOT") or None)
    register_ciks(store)
    docs = load_docs(data_dir / "internal_docs")
    claims = extract_all(docs, store.companies, llm)
    return Context(store=store, docs=docs, claims=claims, jmap=JudgmentMap(store, docs, claims), llm=llm,
                   ledger_path=Path(ledger_path) if ledger_path else None)


def _log(node: str, msg: str, **extra) -> dict:
    return {"node": node, "t": time.strftime("%H:%M:%S"), "msg": msg, **extra}


def _add_evidence(evidence: dict, cid: str, items: list[dict]) -> list[dict]:
    have = evidence.setdefault(cid, [])
    keys = {(e.get("doc_key"), e["sha256"]) for e in have}
    new = []
    for it in items:
        k = (it.get("doc_key"), it["sha256"])
        if k in keys:
            continue
        keys.add(k)
        it = {**it, "id": f"{cid}-E{len(have) + 1}"}
        have.append(it)
        new.append(it)
    return new


# ---------------------------------------------------------------- LLM 보조 (선택)
def _llm_stance(llm, claim: dict, ev: dict) -> dict:
    prompt = ("기관 내부 문서의 주장과 공시 문장을 비교하세요.\n"
              f"주장(국문): {claim['text']}\n주장 토픽: {TOPICS[claim['topic']].label}, 방향: {claim['direction']}\n"
              f"공시 문장(영문): {ev['quote']}\n\n"
              'JSON으로만 답하세요: {"stance": 1(주장과 같은 방향)|-1(반대)|0(관련 있으나 판단 불가), '
              '"forward_looking": true|false (경영진 전망·계획 문장이면 true)}')
    try:
        out = llm.complete_json(prompt, role="extract")
        st = int(out["stance"])
        if st not in (-1, 0, 1):
            raise ValueError("stance")
        return {**ev, "stance": st, "forward_looking": bool(out.get("forward_looking", ev["forward_looking"])),
                "classifier": "llm"}
    except Exception:  # noqa: BLE001 - LLM 실패 시 규칙 결과 유지
        return {**ev, "classifier": "rules (LLM 실패)"}


def _llm_revision(llm, claim: dict, evidence: list[dict], template: str) -> str | None:
    nums = []
    for e in evidence:
        if e.get("metric"):
            m = e["metric"]
            nums.append(f"- {e['label']} {m.get('period', '')}: " + ", ".join(
                f"{k}={m[k]}" for k in ("value", "prev_value", "change_pp", "yoy_pct", "annual_value") if k in m))
        elif e["stance"] == -1:
            nums.append(f"- 공시 문장: {e['quote']}")
    prompt = ("다음 내부 문서 문장의 '사실 전제'만 공시 근거에 맞게 고쳐 쓰세요. 결론·투자의견은 바꾸지 마세요.\n"
              f"원문: {claim['text']}\n근거:\n" + "\n".join(nums) +
              f"\n참고 문안: {template}\n숫자는 근거에 있는 계산값만, 소수 첫째 자리까지 쓰세요.\n"
              'JSON으로만 답하세요: {"revision": "고친 문장"}')
    try:
        return str(llm.complete_json(prompt, role="judge")["revision"]).strip()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 그래프
def build_graph(ctx: Context):
    store = ctx.store

    def planner(state: RunState):
        ev = next(e for e in store.events() if e["id"] == state["event_id"])
        touched = touched_topics(ev, store)
        # 우선순위: XBRL로 확정 가능한 지표 → 공시 본문 언급이 많은 지표
        priority = sorted(touched, key=lambda k: (not touched[k].get("xbrl"), -touched[k]["count"]))
        members = ", ".join(f"{m['form']} {m['filed']}" + ("" if m["in_snapshot"] else " (본문 미수집·목록만)")
                            for m in ev["members"])
        logs = [_log("planner", f"새 공시 감지: {ev['label']}"),
                _log("planner", f"묶음 공시 {len(ev['members'])}건 — {members}"),
                _log("planner", "점검할 지표(우선순위): " + " → ".join(
                    f"{TOPICS[k].label}" + (" [XBRL]" if touched[k].get("xbrl") else f" [본문 {touched[k]['count']}문장]")
                    for k in priority))]
        return {"event": ev, "plan": {"touched": touched, "priority": priority, "map": ctx.jmap.stats()},
                "claims": ctx.claims, "attempts": {}, "evidence": {}, "traces": {}, "calc_notes": {},
                "calculated": [], "verdicts": {}, "conflicts": [], "excluded": [], "verify_rounds": 0,
                "unfinished": [], "llm_calls": 0, "started": time.time(), "log": logs}

    def impact_mapper(state: RunState):
        ev, plan = state["event"], state["plan"]
        hits, paths = ctx.jmap.impacted(ev, plan["touched"])
        order = {k: i for i, k in enumerate(plan["priority"])}
        hits.sort(key=lambda cid: (order.get(paths[cid]["via_topics"][0], 99), cid))
        verdicts = {}
        for cid, c in ctx.claims.items():
            if cid in paths:
                continue
            verdicts[cid] = {"claim_id": cid, "verdict": "영향 없음", "rule": "R0", "evidence_ids": [],
                             "support_groups": [], "contra_groups": [], "revision": None, "needs_more": False,
                             "reason": ("판단 연결 지도에서 이번 공시와 이어진 경로 없음" if c["topic"]
                                        else "6개 주장 유형에 해당하지 않는 문장 (연결 지도 미연결)")}
        by_doc: dict[str, int] = {}
        for cid in hits:
            by_doc[ctx.claims[cid]["doc_id"]] = by_doc.get(ctx.claims[cid]["doc_id"], 0) + 1
        cross = [cid for cid in hits if paths[cid]["relation"] not in ("same",)]
        stats = plan["map"]
        logs = [_log("impact_mapper", f"판단 연결 지도: 문서 {stats['docs']}부 · 문장 {stats['sentences']}개 "
                                      f"(지표 연결 {stats['linked']}개) 중 {len(hits)}개가 이번 공시와 연결"),
                _log("impact_mapper", "문서별 영향 문장: " + ", ".join(
                    f"{ctx.docs_meta[d]['title']} {n}개" for d, n in sorted(by_doc.items()))),
                _log("impact_mapper", f"다른 회사·섹터 문서 경유 {len(cross)}개: " + ", ".join(
                    f"{cid}({paths[cid]['relation_label']})" for cid in cross))]
        return {"impacted": hits, "paths": paths, "pending": list(hits), "verdicts": verdicts, "log": logs}

    def retriever(state: RunState):
        ev = state["event"]
        attempts, traces = dict(state["attempts"]), {k: list(v) for k, v in state["traces"].items()}
        evidence = {k: list(v) for k, v in state["evidence"].items()}
        logs, calls = [], state.get("llm_calls", 0)
        for cid in state["pending"]:
            c = ctx.claims[cid]
            a = attempts.get(cid, 0) + 1
            attempts[cid] = a
            found, trace = retrieve(c, ev, store, a)
            if c["topic"] == "guidance" and a == 1:
                found = guidance_evidence(c, ev, store) + found
            new = _add_evidence(evidence, cid, found)
            if ctx.llm:
                for i, e in enumerate(evidence[cid]):
                    if e in new and e["kind"] == "text":
                        evidence[cid][i] = _llm_stance(ctx.llm, c, e)
                        calls += 1
            prev = traces.get(cid, [])
            trace["new"] = len(new)
            if prev:
                last = prev[-1]
                trace["added_terms"] = [w for w in trace["query"].split() if w not in last["query"].split()]
                trace["removed_terms"] = [w for w in last["query"].split() if w not in trace["query"].split()]
                trace["added_corpus"] = [d for d in trace["corpus"] if d not in last["corpus"]]
            traces.setdefault(cid, []).append(trace)
            if a == 1:
                scope = f"범위 {len(trace['corpus'])}개 공시 ({', '.join(trace['corpus'])})"
            else:
                chg = []
                if trace.get("added_terms"):
                    chg.append("검색어 +" + ",".join(trace["added_terms"]))
                if trace.get("removed_terms"):
                    chg.append("검색어 −" + ",".join(trace["removed_terms"]))
                if trace.get("added_corpus"):
                    chg.append("범위 +" + ", ".join(trace["added_corpus"]))
                scope = " · ".join(chg) or "변경 없음"
            logs.append(_log("retriever", f"{cid} · {a}차 탐색 — {scope} → 새 근거 {len(new)}건",
                             claim=cid, attempt=a))
        return {"attempts": attempts, "traces": traces, "evidence": evidence, "llm_calls": calls, "log": logs}

    def calculator(state: RunState):
        evidence = {k: list(v) for k, v in state["evidence"].items()}
        calc_notes, calculated, logs = dict(state["calc_notes"]), list(state["calculated"]), []
        for cid in state["pending"]:
            if cid in calculated:
                continue
            c = ctx.claims[cid]
            guid = [e for e in evidence.get(cid, []) if e["kind"] == "guidance"]
            items, note = calc_evidence(c, store, guid)
            new = _add_evidence(evidence, cid, items)
            calc_notes[cid] = note
            calculated.append(cid)
            if new:
                m = new[0]["metric"]
                logs.append(_log("calculator", f"{cid} · {note} — 계산식: {m['formula']} · 판단: {new[0]['basis']}",
                                 claim=cid))
        if not logs:
            logs.append(_log("calculator", "이번 차수에 새로 계산할 수치 주장 없음"))
        return {"evidence": evidence, "calc_notes": calc_notes, "calculated": calculated, "log": logs}

    def judge(state: RunState):
        verdicts = dict(state["verdicts"])
        excluded = set(state["excluded"])
        pending, unfinished, logs = [], list(state.get("unfinished", [])), []
        over = time.time() - state["started"] > ctx.budget_s
        for cid in state["impacted"]:
            c = ctx.claims[cid]
            before = verdicts.get(cid, {}).get("verdict")
            v = judge_claim(c, state["evidence"].get(cid, []), state["attempts"].get(cid, 0), excluded)
            v["path"] = state["paths"][cid]
            v["attempts"] = state["attempts"].get(cid, 0)
            if v["needs_more"] and over:
                v["needs_more"] = False
                v["reason"] += " — 시간 한도 도달로 재탐색 중단"
                unfinished.append(cid)
            if v["needs_more"]:
                pending.append(cid)
            old = verdicts.get(cid, {})
            if old.get("revision") and v["verdict"] == "수정" and old.get("evidence_ids") == v["evidence_ids"]:
                v["revision"], v["revision_source"] = old["revision"], old.get("revision_source")
            if old.get("force_template"):
                v["force_template"] = True  # 검증에서 LLM 문안이 반려된 문장은 템플릿 문안으로
            verdicts[cid] = v
            if before != v["verdict"] or v["needs_more"]:
                tail = " → 재탐색" if v["needs_more"] else ""
                logs.append(_log("judge", f"{cid} · {v['verdict']} ({v['rule']}) — {v['reason']}{tail}",
                                 claim=cid, verdict=v["verdict"]))
        conflicts = detect_conflicts(ctx.claims, state["impacted"], ctx.docs_meta, state["evidence"], store)
        for g in conflicts:
            logs.append(_log("judge", f"충돌 {g['id']} · {g['label']}: " + ", ".join(" vs ".join(p) for p in g["pairs"])
                             + f" ({' / '.join(g['teams'])})"))
        if pending:
            logs.append(_log("judge", f"근거 부족 {len(pending)}건 → 검색어·범위를 바꿔 재탐색: {', '.join(pending)}"))
        return {"verdicts": verdicts, "pending": pending, "conflicts": conflicts, "unfinished": unfinished,
                "log": logs}

    def verifier_writer(state: RunState):
        verdicts = {k: dict(v) for k, v in state["verdicts"].items()}
        evidence, excluded = state["evidence"], list(state["excluded"])
        logs, calls = [], state.get("llm_calls", 0)
        for cid, v in verdicts.items():
            if v["verdict"] != "수정" or v.get("revision"):
                continue
            c = ctx.claims[cid]
            evs = [e for e in evidence.get(cid, []) if e["id"] in v["evidence_ids"]]
            tmpl = revision_text(c, evs, store)
            rev, src = tmpl, "template"
            if ctx.llm and tmpl and not v.get("force_template"):
                llm_rev = _llm_revision(ctx.llm, c, evs, tmpl)
                calls += 1
                if llm_rev:
                    rev, src = llm_rev, "llm"
            v["revision"], v["revision_source"] = rev, src
        res = verify(store, evidence, verdicts, set(excluded))
        rounds = state["verify_rounds"] + 1
        if res["failures"] and rounds < MAX_VERIFY_ROUNDS:
            for f in res["failures"]:
                if f["evidence"]:
                    excluded.append(f["evidence"])
                if f["claim"] in verdicts and "수정안" in f["why"]:
                    verdicts[f["claim"]]["revision"] = None
                    verdicts[f["claim"]]["force_template"] = True
            logs.append(_log("verifier_writer", f"검증 실패 {len(res['failures'])}건 → 재판정: " + "; ".join(
                f"{f['claim']} {f['why']}" for f in res["failures"])))
            return {"verdicts": verdicts, "verification": res, "verify_rounds": rounds, "excluded": excluded,
                    "llm_calls": calls, "log": logs}
        status = "통과" if res["passed"] else f"실패 {len(res['failures'])}건 (해당 판정은 확인 필요로 전환)"
        if not res["passed"]:
            for f in res["failures"]:
                if f["claim"] in verdicts:
                    verdicts[f["claim"]].update({"verdict": "확인 필요", "rule": "R4v", "revision": None,
                                                 "reason": f"검증 실패: {f['why']}"})
        logs.append(_log("verifier_writer", f"검증 {status} — 인용 {res['quotes_checked']}건 원문 대조, "
                                            f"수치 {res['numbers_checked']}건 XBRL 원값 재계산, "
                                            f"수정안 수치 {res['revision_numbers_checked']}건 계산값 대조"))
        n_rev = sum(1 for v in verdicts.values() if v["verdict"] == "수정")
        logs.append(_log("verifier_writer", f"빨간펜 수정안 {n_rev}건 · 충돌 리포트 {len(state['conflicts'])}건 작성"))
        return {"verdicts": verdicts, "verification": res, "verify_rounds": rounds, "excluded": excluded,
                "llm_calls": calls, "log": logs}

    def after_judge(state: RunState) -> str:
        return "retriever" if state["pending"] else "verifier_writer"

    def after_verify(state: RunState) -> str:
        return "judge" if not state["verification"]["passed"] and state["verify_rounds"] < MAX_VERIFY_ROUNDS else END

    g = StateGraph(RunState)
    for name, fn in [("planner", planner), ("impact_mapper", impact_mapper), ("retriever", retriever),
                     ("calculator", calculator), ("judge", judge), ("verifier_writer", verifier_writer)]:
        g.add_node(name, fn)
    g.add_edge(START, "planner")
    g.add_edge("planner", "impact_mapper")
    g.add_edge("impact_mapper", "retriever")
    g.add_edge("retriever", "calculator")
    g.add_edge("calculator", "judge")
    g.add_conditional_edges("judge", after_judge, {"retriever": "retriever", "verifier_writer": "verifier_writer"})
    g.add_conditional_edges("verifier_writer", after_verify, {"judge": "judge", END: END})
    return g.compile(checkpointer=InMemorySaver())


def run(ctx: Context, event_id: str, on_step=None, record: bool = True) -> dict:
    """에이전트 1회 실행. on_step(node, update)로 진행 상황을 받을 수 있다."""
    graph = build_graph(ctx)
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    engine = "llm" if ctx.llm else "rules"
    init = {"run_id": run_id, "event_id": event_id, "engine": engine,
            "engine_label": ctx.llm.label if ctx.llm else "규칙 기반 (LLM 미사용)", "log": []}
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": 60}
    path = []
    for chunk in graph.stream(init, config, stream_mode="updates"):
        for node, update in chunk.items():
            path.append(node)
            if on_step:
                on_step(node, update)
    result = dict(graph.get_state(config).values)
    result["node_path"] = path
    result["checkpoints"] = sum(1 for _ in graph.get_state_history(config))
    result["elapsed_s"] = round(time.time() - result["started"], 2)
    if record and ctx.ledger_path:
        Ledger(ctx.ledger_path).append(run_entries(result))
    return result
