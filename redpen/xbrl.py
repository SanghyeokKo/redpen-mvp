"""XBRL 원값 기반 수치 계산 (LLM 계산 금지 원칙: 모든 수치는 여기서만 만든다).

- 분기값: 3개월 기간 행을 그대로 사용
- 4분기: 10-K 연간값 − 3분기 누계(9개월)로 계산 (계산식 기록)
- 매출총이익률, 전년 동기 대비, 전분기 대비, 가이던스 대비 증감률
"""
from __future__ import annotations

import re
from datetime import date

from .store import Store


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _days(r: dict) -> int:
    return (_d(r["end"]) - _d(r["start"])).days if r.get("start") else 0


def cal_quarter(end: str) -> str:
    """기간 종료일 기준 달력 분기 (기간 중앙값이 속한 분기)."""
    e = _d(end)
    mid = date.fromordinal(e.toordinal() - 45)
    return f"CY{mid.year}Q{(mid.month - 1) // 3 + 1}"


def quarterly(store: Store, ticker: str, tag: str) -> list[dict]:
    rows = store.facts(ticker, tag)
    qs = []
    for r in rows:
        if 80 <= _days(r) <= 100:
            qs.append({**r, "derived": False, "formula": f"{tag} {r['start']}~{r['end']} (3개월, {r['form']})",
                       "inputs": [r]})
    for fy in [r for r in rows if r["fp"] == "FY" and _days(r) > 350]:
        ytd = [r for r in rows if r["start"] == fy["start"] and 250 <= _days(r) <= 290]
        if not ytd:
            continue
        y = max(ytd, key=lambda r: r["end"])
        start = date.fromordinal(_d(y["end"]).toordinal() + 1).isoformat()
        if any(q["end"] == fy["end"] for q in qs):
            continue
        qs.append({"start": start, "end": fy["end"], "val": fy["val"] - y["val"], "fy": fy["fy"], "fp": "Q4",
                   "form": fy["form"], "filed": fy["filed"], "accn": fy["accn"], "frame": "", "derived": True,
                   "formula": f"{tag} 연간({fy['start']}~{fy['end']}) − 3분기 누계({y['start']}~{y['end']})",
                   "inputs": [fy, y]})
    return sorted(qs, key=lambda q: q["end"])


def annual(store: Store, ticker: str, tag: str) -> list[dict]:
    return sorted([r for r in store.facts(ticker, tag) if r["fp"] == "FY" and _days(r) > 350], key=lambda r: r["end"])


def _same_quarter_last_year(series: list[dict], q: dict) -> dict | None:
    for p in series:
        gap = (_d(q["end"]) - _d(p["end"])).days
        if 330 <= gap <= 400:
            return p
    return None


def _prev_quarter(series: list[dict], q: dict) -> dict | None:
    prev = [p for p in series if p["end"] < q["end"]]
    return prev[-1] if prev else None


def fiscal_name(q: dict) -> str:
    return f"FY{q['fy']} {q['fp'][1]}분기" if q["fp"].startswith("Q") else f"FY{q['fy']}"


def gross_margin(store: Store, ticker: str) -> dict | None:
    rt = store.revenue_tag(ticker)
    rev, gp = quarterly(store, ticker, rt), quarterly(store, ticker, "GrossProfit")
    if not rev or not gp:
        return None
    gp_by_end = {g["end"]: g for g in gp}
    pairs = [(r, gp_by_end[r["end"]]) for r in rev if r["end"] in gp_by_end]
    if not pairs:
        return None
    r, g = pairs[-1]
    gm = g["val"] / r["val"] * 100
    out = {
        "metric": "gross_margin", "ticker": ticker, "period": fiscal_name(r), "cal": cal_quarter(r["end"]),
        "start": r["start"], "end": r["end"], "value": round(gm, 1), "unit": "%",
        "formula": f"GrossProfit / {rt} = {g['val']/1e6:,.0f} / {r['val']/1e6:,.0f} ($M)",
        "inputs": _inputs(g) + _inputs(r), "derived": r["derived"] or g["derived"],
        "accn": r["accn"], "filed": r["filed"], "form": r["form"],
    }
    prev_r = _same_quarter_last_year(rev, r)
    if prev_r and prev_r["end"] in gp_by_end:
        pg = gp_by_end[prev_r["end"]]
        pgm = pg["val"] / prev_r["val"] * 100
        out.update({"prev_period": fiscal_name(prev_r), "prev_value": round(pgm, 1),
                    "change_pp": round(gm - pgm, 1),
                    "prev_formula": f"GrossProfit / {rt} = {pg['val']/1e6:,.0f} / {prev_r['val']/1e6:,.0f} ($M)",
                    "prev_inputs": _inputs(pg) + _inputs(prev_r)})
    ann_r, ann_g = annual(store, ticker, rt), annual(store, ticker, "GrossProfit")
    if ann_r and ann_g and ann_r[-1]["end"] == ann_g[-1]["end"] and ann_r[-1]["end"] >= r["end"]:
        out.update({"annual_period": f"FY{ann_r[-1]['fy']}",
                    "annual_value": round(ann_g[-1]["val"] / ann_r[-1]["val"] * 100, 1)})
    return out


def revenue_growth(store: Store, ticker: str) -> dict | None:
    rt = store.revenue_tag(ticker)
    rev = quarterly(store, ticker, rt)
    if not rev:
        return None
    q = rev[-1]
    out = {"metric": "revenue_growth", "ticker": ticker, "period": fiscal_name(q), "cal": cal_quarter(q["end"]),
           "value": q["val"], "unit": "USD", "formula": q["formula"], "inputs": _inputs(q), "derived": q["derived"],
           "accn": q["accn"], "filed": q["filed"], "form": q["form"]}
    py = _same_quarter_last_year(rev, q)
    if py:
        out.update({"yoy_pct": round((q["val"] / py["val"] - 1) * 100, 1), "yoy_base": py["val"],
                    "yoy_formula": f"{q['val']/1e6:,.0f} / {py['val']/1e6:,.0f} − 1 ($M, 전년 동기 {fiscal_name(py)})"})
    pq = _prev_quarter(rev, q)
    if pq:
        out.update({"qoq_pct": round((q["val"] / pq["val"] - 1) * 100, 1), "qoq_base": pq["val"],
                    "qoq_formula": f"{q['val']/1e6:,.0f} / {pq['val']/1e6:,.0f} − 1 ($M, 전분기 {fiscal_name(pq)})"})
    return out


_MONEY = re.compile(r"\$([\d.]+)\s*billion\s*±\s*\$([\d.]+)\s*billion")


def guidance_vs_last(store: Store, ticker: str, table: dict) -> dict | None:
    """보도자료 Business Outlook 표의 매출 가이던스 중간값 vs 최근 분기 실제 매출."""
    row = next((r for r in table["rows"] if r[0].lower() == "revenue"), None)
    if not row:
        return None
    m = _MONEY.search(row[1])
    if not m:
        return None
    mid, band = float(m.group(1)) * 1e9, float(m.group(2)) * 1e9
    rg = revenue_growth(store, ticker)
    if not rg:
        return None
    pct = (mid / rg["value"] - 1) * 100
    return {"metric": "guidance_vs_last", "ticker": ticker, "period": table.get("period", ""),
            "value": round(pct, 1), "unit": "%", "guidance_mid": mid, "guidance_band": band,
            "last_period": rg["period"], "last_value": rg["value"],
            "formula": f"가이던스 중간값 {mid/1e9:.1f}B / 최근 분기({rg['period']}) 매출 {rg['value']/1e9:.2f}B − 1",
            "inputs": rg["inputs"], "derived": rg["derived"], "accn": rg["accn"], "filed": rg["filed"],
            "form": rg["form"]}


def inventory_change(store: Store, ticker: str) -> dict | None:
    rows = sorted(store.facts(ticker, "InventoryNet"), key=lambda r: r["end"])
    if len(rows) < 2:
        return None
    a, b = rows[-2], rows[-1]
    return {"metric": "inventory", "ticker": ticker, "value": b["val"], "prev": a["val"],
            "pct": round((b["val"] / a["val"] - 1) * 100, 1), "end": b["end"], "prev_end": a["end"],
            "formula": f"InventoryNet {b['end']} / {a['end']} − 1", "accn": b["accn"], "filed": b["filed"]}


def _inputs(r: dict) -> list[dict]:
    src = r.get("inputs") or [r]
    return [{k: x[k] for k in ("start", "end", "val", "fy", "fp", "form", "filed", "accn")} for x in src]


def recompute(store: Store, metric: dict) -> float | None:
    """검증용 재계산: 원값(raw facts)을 다시 찾아 같은 수치가 나오는지 확인."""
    t = metric["ticker"]
    if metric["metric"] == "gross_margin":
        again = gross_margin(store, t)
        return again["value"] if again else None
    if metric["metric"] == "revenue_growth":
        again = revenue_growth(store, t)
        return again.get("yoy_pct") if again else None
    return None
