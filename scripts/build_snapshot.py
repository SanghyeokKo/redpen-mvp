"""EDGAR에서 스냅샷을 다시 만든다 (실시간 모드).

    SEC_USER_AGENT="팀명 연락처이메일" python scripts/build_snapshot.py --out data/snapshot_live

- data/companies.yaml의 모니터링 기업별로 공시 목록·XBRL·최신 정기공시 MD&A·실적 보도자료(Ex.99.1)를 내려받아
  data/snapshot과 같은 형식으로 저장한다. 데모 앱은 REDPEN_SNAPSHOT 환경 변수로 다른 스냅샷을 가리킬 수 있다.
- SEC 공정 접근 정책: User-Agent 명시, 초당 10회 이하(기본 8회), 응답 캐시(.cache/edgar).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redpen.edgar import (FILING_COLUMNS, XBRL_COLUMNS, EdgarClient, extract_guidance, extract_mdna,  # noqa: E402
                          html_to_lines, recent_filings, xbrl_rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "snapshot_live"))
    ap.add_argument("--companies", default=str(ROOT / "data" / "companies.yaml"))
    args = ap.parse_args()
    out = Path(args.out)
    (out / "sections").mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load(Path(args.companies).read_text(encoding="utf-8"))
    client = EdgarClient(cache_dir=ROOT / ".cache" / "edgar")

    filings = {"_source": "https://data.sec.gov/submissions/CIK##########.json (filings.recent)",
               "_columns": FILING_COLUMNS}
    xbrl = {"_source": "https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json", "_columns": XBRL_COLUMNS}
    docs = []
    for t, c in cfg["monitored"].items():
        sub = client.submissions(c["cik"])
        rows = recent_filings(sub, n=12)
        filings[t] = {"name": sub.get("name"), "cik": c["cik"], "fiscalYearEnd": sub.get("fiscalYearEnd"),
                      "rows": [[r[k] for k in FILING_COLUMNS] for r in rows]}
        xbrl[t] = xbrl_rows(client.companyfacts(c["cik"]))
        periodic = next((r for r in rows if r["form"] in ("10-Q", "10-K")), None)
        if not periodic:
            continue
        html = client.document(c["cik"], periodic["accessionNumber"], periodic["primaryDocument"])
        paras = extract_mdna(html_to_lines(html), periodic["form"])
        docs.append(_save(out, t, periodic, c["cik"], periodic["primaryDocument"],
                          "Item 7. MD&A" if periodic["form"] == "10-K" else "Item 2. MD&A", paras, []))
        for r in rows:  # 직전 45일 이내 실적 8-K(Item 2.02)의 보도자료
            if r["form"] != "8-K" or "2.02" not in (r["items"] or ""):
                continue
            gap = (date.fromisoformat(periodic["filingDate"]) - date.fromisoformat(r["filingDate"])).days
            if not 0 <= gap <= 45:
                continue
            idx = client.filing_index(c["cik"], r["accessionNumber"])
            ex = next((f["name"] for f in idx.get("directory", {}).get("item", [])
                       if "99" in f["name"].lower() and f["name"].lower().endswith((".htm", ".html"))), None)
            if not ex:
                continue
            lines = html_to_lines(client.document(c["cik"], r["accessionNumber"], ex))
            paras = [{"sub": "Release", "text": x} for x in lines if len(x) >= 80][:60]
            docs.append(_save(out, t, r, c["cik"], ex, "Exhibit 99.1 Press Release", paras, extract_guidance(lines)))
    (out / "filings.json").write_text(json.dumps(filings, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "xbrl.json").write_text(json.dumps(xbrl, ensure_ascii=False), encoding="utf-8")
    manifest = {"name": "RedPen 실시간 스냅샷", "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "method": "scripts/build_snapshot.py (SEC EDGAR API, User-Agent 명시, 초당 8회 이하)",
                "license": "미국 SEC 공개 데이터", "companies": list(cfg["monitored"]), "documents": docs,
                "sec_calls": client.calls}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {out} · 문서 {len(docs)}건 · SEC 호출 {client.calls}회")


def _save(out: Path, t: str, r: dict, cik: str, name: str, section: str, paras: list, tables: list) -> dict:
    key = f"{t}_{r['form']}_{r['accessionNumber']}"
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{r['accessionNumber'].replace('-', '')}/{name}"
    body = {"company": t, "form": r["form"], "items": r["items"], "accession": r["accessionNumber"],
            "filed": r["filingDate"], "period": r["reportDate"], "url": url, "section": section,
            "note": "scripts/build_snapshot.py 자동 추출 (원문 그대로)", "paragraphs": paras, "tables": tables}
    (out / "sections" / f"{key}.json").write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"key": key, "desc": f"{t} {r['form']} {r['filingDate']} {section} ({len(paras)}단락)"}


if __name__ == "__main__":
    main()
