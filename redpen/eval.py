"""정답셋 대비 평가: 영향 문장 탐지율·오탐률, 판정 정확도, 충돌 탐지, 수치 오류."""
from __future__ import annotations

from pathlib import Path

import yaml

VERDICTS = ["수정", "유지", "확인 필요", "영향 없음"]


def load_gold(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["events"]


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def evaluate(result: dict, gold_events: dict) -> dict | None:
    g = gold_events.get(result["event"]["id"])
    if not g:
        return None
    gv = g["verdicts"]
    pv = {k: v["verdict"] for k, v in result["verdicts"].items()}
    ids = sorted(gv)
    gold_imp = {k for k in ids if gv[k] != "영향 없음"}
    pred_imp = {k for k in ids if pv.get(k, "영향 없음") != "영향 없음"}
    tp = len(gold_imp & pred_imp)
    no_imp = [k for k in ids if gv[k] == "영향 없음"]
    fp_no = [k for k in no_imp if k in pred_imp]
    correct = [k for k in ids if pv.get(k) == gv[k]]
    confusion = {a: {b: 0 for b in VERDICTS} for a in VERDICTS}
    for k in ids:
        confusion[gv[k]][pv.get(k, "영향 없음")] += 1
    gold_pairs = {frozenset(p) for p in g.get("conflicts", [])}
    pred_pairs = {frozenset(p) for grp in result["conflicts"] for p in grp["pairs"]}
    cp = len(gold_pairs & pred_pairs)
    rev_gold = {k for k in ids if gv[k] == "수정"}
    rev_pred = {k for k in ids if pv.get(k) == "수정"}
    wrong_rev = sorted(rev_pred - rev_gold)
    return {
        "event_id": result["event"]["id"], "n_sentences": len(ids),
        "impact_recall": _ratio(tp, len(gold_imp)), "impact_precision": _ratio(tp, len(pred_imp)),
        "no_impact_fp_rate": _ratio(len(fp_no), len(no_imp)), "no_impact_fp": fp_no,
        "verdict_accuracy": _ratio(len(correct), len(ids)),
        "mismatches": [{"id": k, "gold": gv[k], "pred": pv.get(k)} for k in ids if pv.get(k) != gv[k]],
        "conflict_recall": _ratio(cp, len(gold_pairs)), "conflict_precision": _ratio(cp, len(pred_pairs)),
        "wrong_revisions": wrong_rev, "wrong_revision_rate": _ratio(len(wrong_rev), len(rev_pred)),
        "numeric_errors": sum(1 for f in result["verification"]["failures"] if "수치" in f["why"] or "XBRL" in f["why"]),
        "confusion": confusion,
    }
