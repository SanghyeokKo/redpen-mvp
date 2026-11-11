"""검증 (Verifier): 인용·수치·근거 ID를 원 데이터와 다시 대조한다."""
from __future__ import annotations

import re

from . import xbrl
from .store import Store


def _raw_has(store: Store, ticker: str, inp: dict) -> bool:
    for tag_rows in store.xbrl_raw.get(ticker, {}).values():
        for r in tag_rows:
            if r[0] == inp["start"] and r[1] == inp["end"] and r[2] == inp["val"] and r[7] == inp["accn"]:
                return True
    return False


def verify(store: Store, evidence: dict, verdicts: dict, excluded: set) -> dict:
    failures, n_quote, n_num, n_rev = [], 0, 0, 0
    for cid, evs in evidence.items():
        for e in evs:
            if e["id"] in excluded:
                continue
            if e["kind"] == "text":
                n_quote += 1
                para = store.docs[e["doc_key"]].paragraphs[e["paragraph_index"]]["text"]
                if e["quote"] not in para:
                    failures.append({"evidence": e["id"], "claim": cid, "why": "인용문이 원문 단락에 없음"})
            elif e["kind"] == "guidance":
                n_quote += 1
                tables = store.docs[e["doc_key"]].tables
                if e["table"] not in tables:
                    failures.append({"evidence": e["id"], "claim": cid, "why": "가이던스 표가 원문과 다름"})
            elif e["kind"] in ("xbrl", "calc"):
                n_num += 1
                m = e["metric"]
                if m["metric"] == "gross_margin":
                    again = xbrl.gross_margin(store, m["ticker"])
                    ok = again is not None and again["value"] == m["value"] and again.get("prev_value") == m.get("prev_value")
                elif m["metric"] == "revenue_growth":
                    again = xbrl.revenue_growth(store, m["ticker"])
                    ok = again is not None and again.get("yoy_pct") == m.get("yoy_pct")
                else:
                    ok = True
                ok = ok and all(_raw_has(store, m["ticker"], i) for i in m.get("inputs", []))
                if not ok:
                    failures.append({"evidence": e["id"], "claim": cid, "why": "XBRL 원값 재계산 불일치"})
    for cid, v in verdicts.items():
        if v["verdict"] in ("수정", "유지") and not v["evidence_ids"]:
            failures.append({"evidence": None, "claim": cid, "why": "근거 ID 없는 판정"})
        if v.get("revision"):
            allowed = set()
            for e in evidence.get(cid, []):
                m = e.get("metric") or {}
                for k in ("value", "prev_value", "annual_value", "change_pp", "yoy_pct"):
                    if isinstance(m.get(k), (int, float)):
                        allowed.add(f"{abs(m[k]):.1f}")
            for num in re.findall(r"(\d+(?:\.\d+)?)%", v["revision"]):
                n_rev += 1
                if f"{float(num):.1f}" not in allowed:
                    failures.append({"evidence": None, "claim": cid, "why": f"수정안 수치 {num}%가 계산값과 다름"})
    return {"quotes_checked": n_quote, "numbers_checked": n_num, "revision_numbers_checked": n_rev,
            "failures": failures, "passed": not failures}
