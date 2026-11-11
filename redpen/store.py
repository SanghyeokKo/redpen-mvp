"""EDGAR 스냅샷 저장소: 공시 목록, XBRL 원값, 공시 본문 단락, 공시 이벤트."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"


@dataclass
class FilingDoc:
    key: str
    company: str
    form: str
    accession: str
    filed: str
    period: str
    url: str
    section: str
    note: str
    paragraphs: list = field(default_factory=list)   # [{idx, sub, text, ctx_years}]
    tables: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.company} {self.form} ({self.filed})"


def _years(text: str) -> list[int]:
    return [int(y) for y in re.findall(r"\b(20\d\d)\b", text)]


class Store:
    def __init__(self, data_dir: str | Path, snapshot_dir: str | Path | None = None):
        self.data_dir = Path(data_dir)
        cfg = yaml.safe_load((self.data_dir / "companies.yaml").read_text(encoding="utf-8"))
        self.companies: dict = cfg["monitored"]
        self.relations: list = cfg["relations"]
        snap = Path(snapshot_dir) if snapshot_dir else self.data_dir / "snapshot"
        self.snapshot_dir = snap
        self.manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
        self.filings_raw = json.loads((snap / "filings.json").read_text(encoding="utf-8"))
        self.xbrl_raw = json.loads((snap / "xbrl.json").read_text(encoding="utf-8"))
        self.docs: dict[str, FilingDoc] = {}
        for p in sorted((snap / "sections").glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            fd = FilingDoc(key=p.stem, company=d["company"], form=d["form"], accession=d["accession"],
                           filed=d["filed"], period=d["period"], url=d["url"], section=d["section"],
                           note=d.get("note", ""), tables=d.get("tables", []))
            ctx: list[int] = []
            last_sub = None
            for i, para in enumerate(d["paragraphs"]):
                text = para["text"]
                if para["sub"] != last_sub:
                    ctx, last_sub = [], para["sub"]
                is_bullet = text.startswith("•")
                if not is_bullet:
                    ctx = _years(text)  # 글머리표가 아닌 단락의 연도를 다음 글머리표들이 물려받음
                fd.paragraphs.append({"idx": i, "sub": para["sub"], "text": text,
                                      "ctx_years": ctx if is_bullet else []})
            self.docs[fd.key] = fd

    # ------------------------------------------------------------ 공시 목록
    def filings(self, ticker: str) -> list[dict]:
        cols = self.filings_raw["_columns"]
        return [dict(zip(cols, r)) for r in self.filings_raw[ticker]["rows"]]

    def doc_by_accession(self, accession: str) -> FilingDoc | None:
        for d in self.docs.values():
            if d.accession == accession:
                return d
        return None

    def filing_url(self, ticker: str, row: dict) -> str:
        cik = str(int(self.companies[ticker]["cik"]))
        return SEC_ARCHIVE.format(cik=cik, acc=row["accessionNumber"].replace("-", ""), doc=row["primaryDocument"])

    def latest_docs(self, ticker: str) -> list[FilingDoc]:
        """해당 회사의 최신 정기공시(10-Q/10-K)와 실적 8-K 중 스냅샷에 본문이 있는 것."""
        out = [d for d in self.docs.values() if d.company == ticker]
        return sorted(out, key=lambda d: d.filed, reverse=True)

    # ------------------------------------------------------------ 공시 이벤트 (새 공시 감지)
    def events(self) -> list[dict]:
        """회사별 최신 정기공시 + 직전 45일 내 실적 8-K(Item 2.02)를 한 이벤트로 묶는다."""
        evs = []
        for t, c in self.companies.items():
            rows = self.filings(t)
            periodic = [r for r in rows if r["form"] in ("10-Q", "10-K")]
            if not periodic:
                continue
            main = periodic[0]
            bundle = [main]
            for r in rows:
                if r["form"] == "8-K" and "2.02" in r["items"]:
                    gap = (date.fromisoformat(main["filingDate"]) - date.fromisoformat(r["filingDate"])).days
                    if 0 <= gap <= 45:
                        bundle.append(r)
            fy_label = self.fiscal_label(t, main["accessionNumber"])
            members = []
            for r in bundle:
                doc = self.doc_by_accession(r["accessionNumber"])
                members.append({"form": r["form"], "filed": r["filingDate"], "accession": r["accessionNumber"],
                                "items": r["items"], "url": self.filing_url(t, r),
                                "in_snapshot": doc is not None, "doc_key": doc.key if doc else None})
            evs.append({
                "id": f"{t}-{main['accessionNumber']}", "company": t, "role": c["role"],
                "name": c["name"], "name_ko": c["name_ko"], "fiscal": fy_label,
                "filed": main["filingDate"], "period": main["reportDate"], "members": members,
                "label": f"{c['name_ko']} {fy_label} 실적 공시 ({main['form']} {main['filingDate']})",
            })
        return sorted(evs, key=lambda e: e["filed"], reverse=True)

    def fiscal_label(self, ticker: str, accession: str) -> str:
        for tag_rows in self.xbrl_raw.get(ticker, {}).values():
            for r in tag_rows:
                if r[7] == accession:
                    fy, fp = r[3], r[4]
                    return f"FY{fy} 4분기·연간" if fp == "FY" else f"FY{fy} {fp[1]}분기"
        return ""

    # ------------------------------------------------------------ XBRL
    def facts(self, ticker: str, tag: str) -> list[dict]:
        cols = self.xbrl_raw["_columns"]
        return [dict(zip(cols, r)) for r in self.xbrl_raw.get(ticker, {}).get(tag, [])]

    def revenue_tag(self, ticker: str) -> str:
        return self.companies[ticker]["revenue_tag"]

    # ------------------------------------------------------------ 관계
    def relation_type(self, src: str, dst: str) -> str | None:
        """src 회사의 공시가 dst 회사 판단에 미치는 관계 방향."""
        if src == dst:
            return "same"
        for r in self.relations:
            if r["type"] == "supplier" and r["source"] == src and r["target"] == dst:
                return "supplier_to_customer"
            if r["type"] == "supplier" and r["source"] == dst and r["target"] == src:
                return "customer_to_supplier"
            if r["type"] == "competitor" and {r["source"], r["target"]} == {src, dst}:
                return "competitor"
        return None

    def name_ko(self, ticker: str) -> str:
        if ticker == "SECTOR":
            return "섹터 전체"
        return self.companies.get(ticker, {}).get("name_ko", ticker)
