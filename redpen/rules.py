"""판정 기준 (Judge 규칙) — 기획서 ③의 판정 기준표를 코드로 옮긴 것.

  유지      공시가 문장의 전제와 같은 방향 → 문장은 그대로, 근거만 추가 (근거 강화)
  수정      사실 전제(수치·방향)가 XBRL 원값·공시 본문과 반대이고, 서로 독립된 근거 2개 이상 → 사실 전제만 고친 수정안
  확인 필요 근거가 1개뿐이거나, 가이던스·경영진 발언처럼 해석 여지가 있거나, 재탐색 3회 후에도 근거 없음
  충돌      같은 (기업·지표·기간)에 연결된 두 문장이 반대 방향 → 최신 공시 근거와 함께 양쪽 담당자에게 전달
  영향 없음 판단 연결 지도상 공시와 연결되지 않은 문장 → 판정하지 않음
"""
from __future__ import annotations

from . import xbrl
from .retrieval import sha
from .store import Store
from .topics import TOPICS

MAX_ATTEMPTS = 3


# ---------------------------------------------------------------- 수치 확인 (Calculator)
def calc_evidence(claim: dict, store: Store, guidance_items: list[dict]) -> tuple[list[dict], str]:
    """주장에 필요한 수치를 XBRL 원값으로 계산해 근거로 만든다. (근거, 로그 메모)"""
    topic, t = claim["topic"], claim["company"]
    if t not in store.companies:
        return [], "섹터 문장: 회사별 XBRL 대상 아님"
    if topic == "gross_margin":
        gm = xbrl.gross_margin(store, t)
        if not gm:
            return [], "XBRL 매출총이익 데이터 없음"
        num = claim.get("numeric")
        if num:
            v = gm["value"]
            inside = ((num["op"] == "range" and num["lo"] <= v < num["hi"]) or (num["op"] == "lt" and v < num["hi"])
                      or (num["op"] == "gt" and v > num["lo"]) or (num["op"] == "approx" and num["lo"] <= v <= num["hi"]))
            stance = 1 if inside else -1
            direction = 0
            basis = f"문장 수치 '{num['raw']}' vs 계산값 {v:.1f}%"
        else:
            ch = gm.get("change_pp", 0.0)
            direction = 1 if ch > 0.5 else -1 if ch < -0.5 else 0
            stance = 0 if direction == 0 or claim["direction"] == 0 else (1 if direction == claim["direction"] else -1)
            basis = f"전년 동기 대비 {ch:+.1f}%p"
        ev = _calc_item(claim, gm, "xbrl", stance, direction, basis)
        return [ev], f"매출총이익률 {gm['period']} {gm['value']:.1f}% ({'4분기 = 연간 − 3분기 누계' if gm['derived'] else '3개월 원값'})"
    if topic == "revenue_growth" and not claim.get("segment"):
        rg = xbrl.revenue_growth(store, t)
        if not rg or "yoy_pct" not in rg:
            return [], "XBRL 매출 데이터 없음"
        direction = 1 if rg["yoy_pct"] > 0 else -1
        stance = 0 if claim["direction"] == 0 else (1 if direction == claim["direction"] else -1)
        return [_calc_item(claim, rg, "xbrl", stance, direction, f"전년 동기 대비 {rg['yoy_pct']:+.1f}%")], \
            f"매출 {rg['period']} 전년 동기 대비 {rg['yoy_pct']:+.1f}%"
    if topic == "revenue_growth" and claim.get("segment"):
        return [], f"세그먼트({claim['segment']}) 매출은 XBRL 회사 단위 항목이 아님 → 본문 근거만 사용"
    if topic == "datacenter_growth":
        return [], "데이터센터 부문 매출은 XBRL 회사 단위 항목이 아님 → 본문 근거만 사용"
    if topic == "guidance" and guidance_items:
        g = xbrl.guidance_vs_last(store, t, guidance_items[0]["table"])
        if not g:
            return [], "가이던스 표 해석 실패"
        direction = 1 if g["value"] > 0 else -1
        stance = 0 if claim["direction"] == 0 else (1 if direction == claim["direction"] else -1)
        ev = _calc_item(claim, g, "calc", stance, direction, f"가이던스 중간값 vs 최근 분기 {g['value']:+.1f}%")
        ev["forward_looking"] = True
        ev["group_hint"] = guidance_items[0]["accession"]
        return [ev], f"가이던스 {g['period']} 중간값이 {g['last_period']} 대비 {g['value']:+.1f}%"
    return [], "수치 계산 대상 아님"


def _calc_item(claim, metric, kind, stance, direction, basis):
    label = {"gross_margin": "매출총이익률", "revenue_growth": "매출", "guidance_vs_last": "가이던스 대비 증감"}[metric["metric"]]
    return {
        "kind": kind, "company": metric["ticker"], "accession": metric["accn"], "form": metric["form"],
        "filed": metric["filed"], "url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{claim_cik(metric)}.json",
        "doc_key": None, "paragraph_index": -1,
        "location": f"XBRL companyfacts · {metric.get('period', '')}", "quote": "",
        "metric": metric, "label": label, "basis": basis,
        "direction": direction, "stance": stance, "forward_looking": False, "attempt": 0,
        "sha256": sha(repr(sorted((k, str(v)) for k, v in metric.items() if k != "inputs"))),
    }


_CIK = {}


def claim_cik(metric):
    return _CIK.get(metric["ticker"], "")


_NAME_KO: dict[str, str] = {}


def register_ciks(store: Store):
    for t, c in store.companies.items():
        _CIK[t] = c["cik"]
        _NAME_KO[t] = c["name_ko"]


# ---------------------------------------------------------------- 판정 (Judge)
def group_key(ev: dict) -> str:
    """독립 근거 단위: 서로 다른 공시이거나, 같은 공시라도 근거 유형(XBRL 수치 / 본문 서술)이 다르면 독립."""
    if ev["kind"] in ("xbrl",):
        return f"xbrl:{ev['company']}"
    if ev["kind"] in ("calc", "guidance"):
        return f"guidance:{ev.get('group_hint') or ev['accession']}"
    return f"text:{ev['accession']}"


def judge_claim(claim: dict, evidence: list[dict], attempts: int, excluded: set) -> dict:
    ev = [e for e in evidence if e["id"] not in excluded]
    sup = {group_key(e) for e in ev if e["stance"] == 1}
    con = {group_key(e) for e in ev if e["stance"] == -1}
    neu = [e for e in ev if e["stance"] == 0]
    t = TOPICS[claim["topic"]]
    ids = [e["id"] for e in ev]
    v = {"claim_id": claim["sentence_id"], "evidence_ids": ids, "support_groups": sorted(sup),
         "contra_groups": sorted(con), "revision": None, "needs_more": False}

    if not ev:
        v["needs_more"] = attempts < MAX_ATTEMPTS
        return {**v, "verdict": "확인 필요", "rule": "R4e", "reason": f"{attempts}차 탐색까지 근거를 찾지 못했습니다." + (" 재탐색 3회를 마쳐 담당자에게 넘깁니다." if attempts >= MAX_ATTEMPTS else "")}
    if t.interpretive or (not sup and not con):
        return {**v, "verdict": "확인 필요", "rule": "R4d",
                "reason": f"{t.label}은(는) 해석이 필요한 판단이라, 관련 근거 {len(neu)}건을 정리해 담당자에게 넘깁니다."}
    if sup and con:
        return {**v, "verdict": "확인 필요", "rule": "R4c", "reason": f"근거가 엇갈립니다. 같은 방향 {len(sup)}건, 반대 방향 {len(con)}건입니다."}
    if sup:
        return {**v, "verdict": "유지", "rule": "R2",
                "reason": f"공시가 문장의 전제와 같은 방향입니다. 독립된 근거 {len(sup)}건을 더해 근거를 보강했습니다."}
    # 여기부터 반대 근거만 있는 경우
    if claim["modality"] == "forecast":
        return {**v, "verdict": "확인 필요", "rule": "R4b",
                "reason": f"전망 문장이라 담당자가 판단합니다. 최신 공시에는 반대 방향 근거가 {len(con)}건 있습니다."}
    # 사실 전제를 뒤집는 근거는 '사실·계산값'만 센다 (경영진 전망 문장은 가설)
    con_fact = {group_key(e) for e in ev if e["stance"] == -1 and not e.get("forward_looking")}
    v["contra_fact_groups"] = sorted(con_fact)
    if len(con_fact) >= 2:
        return {**v, "verdict": "수정", "rule": "R3",
                "reason": f"사실 전제가 공시와 반대입니다. 서로 독립된 근거 {len(con_fact)}건: "
                          f"{', '.join(_group_name(g, ev) for g in sorted(con_fact))}."}
    if con - con_fact:
        return {**v, "verdict": "확인 필요", "rule": "R4b",
                "reason": "반대 근거 일부가 가이던스나 경영진 전망(가설)이라 해석의 여지가 있습니다."}
    v["needs_more"] = attempts < MAX_ATTEMPTS
    why = "반대 근거가 1건뿐이라 고치지 않았습니다 (수정에는 독립 근거 2건 필요)."
    if attempts >= MAX_ATTEMPTS:
        why += f" 재탐색 {attempts}회를 마쳐 담당자에게 넘깁니다."
    return {**v, "verdict": "확인 필요", "rule": "R4a", "reason": why}


def _group_name(g: str, ev: list[dict] | None = None) -> str:
    """독립 근거 묶음을 사람이 읽는 이름으로: 'text:0001045810-…' → '엔비디아 10-Q 본문'."""
    kind, _, rest = g.partition(":")
    if kind == "xbrl":
        return f"{_NAME_KO.get(rest, rest)} XBRL 수치"
    e = next((x for x in ev or [] if x["accession"] == rest), None)
    if not e:
        return {"text": "공시 본문", "guidance": "가이던스"}.get(kind, kind) + f" {rest}"
    name = _NAME_KO.get(e["company"], e["company"])
    return f"{name} {e['form']} 본문" if kind == "text" else f"{name} 가이던스"


# ---------------------------------------------------------------- 수정안 (사실 전제만 고침)
def revision_text(claim: dict, evidence: list[dict], store: Store) -> str | None:
    name = store.name_ko(claim["company"])
    topic = claim["topic"]
    metric = next((e["metric"] for e in evidence if e.get("metric") and e["metric"]["metric"] == "gross_margin"), None)
    if topic == "gross_margin" and metric and "prev_value" in metric:
        word = "상승" if metric["change_pp"] > 0 else "하락"
        s = (f"{name}의 매출총이익률은 {metric['period']} {metric['value']:.1f}%로 "
             f"전년 동기({metric['prev_value']:.1f}%) 대비 {abs(metric['change_pp']):.1f}%p {word}했다")
        if metric.get("annual_value") is not None:
            s += f"({metric['annual_period']} 연간 {metric['annual_value']:.1f}%)"
        return s + "."
    dirs = [e["direction"] for e in evidence if e["stance"] == -1 and e["direction"]]
    d = 1 if sum(dirs) >= 0 else -1
    if topic == "memory_balance":
        return ("AI 수요 증가가 업계 공급 증가를 앞지르며 메모리·부품 공급 제약이 이어지고 있다." if d > 0
                else "메모리 공급이 수요를 웃돌며 수급이 완화되고 있다.")
    if topic == "ai_demand":
        return "AI·데이터센터 수요 증가세가 이어지고 있다." if d > 0 else "AI·데이터센터 수요 증가세가 둔화되고 있다."
    if topic == "revenue_growth":
        rg = next((e["metric"] for e in evidence if e.get("metric") and e["metric"]["metric"] == "revenue_growth"), None)
        if rg and "yoy_pct" in rg:
            return f"{name}의 매출은 {rg['period']} 전년 동기 대비 {abs(rg['yoy_pct']):.1f}% {'증가' if rg['yoy_pct'] > 0 else '감소'}했다."
    if topic == "datacenter_growth":
        return f"{name}의 데이터센터 매출은 {'증가세가 이어지고' if d > 0 else '감소하고'} 있다."
    return None


# ---------------------------------------------------------------- 충돌 검사
def detect_conflicts(claims: dict, impacted: list[str], docs_meta: dict, evidence: dict, store: Store) -> list[dict]:
    by_topic: dict[str, list[dict]] = {}
    for cid in impacted:
        c = claims[cid]
        if c["topic"] and c["direction"] and not TOPICS[c["topic"]].interpretive:
            by_topic.setdefault(c["topic"], []).append(c)
    groups = []
    for topic, cs in by_topic.items():
        pairs = []
        for i, a in enumerate(cs):
            for b in cs[i + 1:]:
                if a["doc_id"] == b["doc_id"] or a["direction"] == b["direction"]:
                    continue
                if _overlap(a, b, topic, store):
                    pairs.append((a["sentence_id"], b["sentence_id"]))
        if not pairs:
            continue
        members = sorted({x for p in pairs for x in p})
        plus = [m for m in members if claims[m]["direction"] > 0]
        minus = [m for m in members if claims[m]["direction"] < 0]
        # 최신 공시 근거가 어느 쪽 방향인지 (독립 근거 단위로 집계)
        seen, score = set(), {1: 0, -1: 0}
        for m in members:
            for e in evidence.get(m, []):
                if e["direction"] and group_key(e) not in seen:
                    seen.add(group_key(e))
                    score[e["direction"]] += 1
        favored = 1 if score[1] > score[-1] else -1 if score[-1] > score[1] else 0
        teams = sorted({docs_meta[claims[m]["doc_id"]]["team"] for m in members})
        groups.append({"id": f"K{len(groups) + 1}", "topic": topic, "label": TOPICS[topic].label,
                       "pairs": [list(p) for p in pairs], "plus": plus, "minus": minus,
                       "evidence_plus": score[1], "evidence_minus": score[-1], "favored": favored,
                       "teams": teams, "dir_labels": TOPICS[topic].dir_labels})
    return groups


def _overlap(a: dict, b: dict, topic: str, store: Store) -> bool:
    if a["company"] == b["company"] or "SECTOR" in (a["company"], b["company"]):
        return True
    return TOPICS[topic].scope == "market" and store.relation_type(a["company"], b["company"]) is not None
