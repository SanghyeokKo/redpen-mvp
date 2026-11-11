"""SEC EDGAR 실시간 클라이언트 (선택 기능).

- SEC 공정 접근 정책: User-Agent에 이름·연락처 명시(SEC_USER_AGENT 환경 변수), 초당 10회 이하 호출
- 오류 시 지수 백오프 재시도, 응답은 디스크 캐시
- 데모 앱은 기본적으로 data/snapshot(2026-11-11 수집본)을 쓰고, 이 모듈은 새 공시 확인·스냅샷 재생성에 쓴다.
"""
from __future__ import annotations

import gzip
import hashlib
import html as htmllib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

DATA_API = "https://data.sec.gov"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
XBRL_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "GrossProfit", "InventoryNet"]
XBRL_COLUMNS = ["start", "end", "val", "fy", "fp", "form", "filed", "accn", "frame"]
FILING_COLUMNS = ["form", "filingDate", "reportDate", "accessionNumber", "primaryDocument", "items"]


class EdgarClient:
    def __init__(self, user_agent: str | None = None, cache_dir: str | Path | None = None, max_rps: float = 8.0,
                 opener=None):
        self.ua = user_agent or os.getenv("SEC_USER_AGENT", "").strip()
        if not self.ua:
            raise ValueError("SEC_USER_AGENT 환경 변수가 필요합니다 (예: 'MONDAI RedPen contact@example.com').")
        self.min_interval = 1.0 / max_rps
        self._last = 0.0
        self.cache = Path(cache_dir) if cache_dir else None
        self._open = opener or urllib.request.urlopen
        self.calls = 0

    def _get(self, url: str, retries: int = 4, use_cache: bool = True) -> bytes:
        cpath = None
        if self.cache and use_cache:
            cpath = self.cache / hashlib.sha256(url.encode()).hexdigest()[:24]
            if cpath.exists():
                return cpath.read_bytes()
        for i in range(retries):
            wait = self._last + self.min_interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "gzip"})
            try:
                self.calls += 1
                with self._open(req, timeout=30) as r:
                    raw = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                if cpath:
                    cpath.parent.mkdir(parents=True, exist_ok=True)
                    cpath.write_bytes(raw)
                return raw
            except urllib.error.HTTPError as e:
                if e.code in (403, 404) or i == retries - 1:
                    raise
                time.sleep(2 ** i)  # 429·5xx: 지수 백오프
            except urllib.error.URLError:
                if i == retries - 1:
                    raise
                time.sleep(2 ** i)
        raise RuntimeError("unreachable")

    def json(self, url: str, use_cache: bool = True) -> dict:
        return json.loads(self._get(url, use_cache=use_cache))

    def submissions(self, cik: str, use_cache: bool = False) -> dict:
        return self.json(f"{DATA_API}/submissions/CIK{cik}.json", use_cache=use_cache)

    def companyfacts(self, cik: str) -> dict:
        return self.json(f"{DATA_API}/api/xbrl/companyfacts/CIK{cik}.json")

    def filing_index(self, cik: str, accession: str) -> dict:
        return self.json(f"{ARCHIVES}/{int(cik)}/{accession.replace('-', '')}/index.json")

    def document(self, cik: str, accession: str, name: str) -> str:
        return self._get(f"{ARCHIVES}/{int(cik)}/{accession.replace('-', '')}/{name}").decode("utf-8", "replace")


# ---------------------------------------------------------------- 공시 목록 · 새 공시 감지
def recent_filings(sub: dict, forms=("10-Q", "10-K", "8-K"), n: int = 15) -> list[dict]:
    r = sub["filings"]["recent"]
    rows = []
    for i in range(len(r["form"])):
        if r["form"][i] in forms:
            rows.append({k: r[k][i] for k in FILING_COLUMNS})
        if len(rows) >= n:
            break
    return rows


def is_earnings_filing(row: dict) -> bool:
    return row["form"] in ("10-Q", "10-K") or (row["form"] == "8-K" and "2.02" in (row.get("items") or ""))


def check_new_filings(store, client: EdgarClient) -> list[dict]:
    """스냅샷에 없는 실적 관련 공시(10-Q·10-K·8-K Item 2.02)를 찾는다."""
    out = []
    for t, c in store.companies.items():
        known = {r["accessionNumber"] for r in store.filings(t)}
        for r in recent_filings(client.submissions(c["cik"])):
            if r["accessionNumber"] not in known and is_earnings_filing(r):
                out.append({"company": t, **r})
    return out


# ---------------------------------------------------------------- XBRL
def xbrl_rows(facts: dict, tags=XBRL_TAGS) -> dict:
    out = {}
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        units = gaap.get(tag, {}).get("units", {}).get("USD", [])
        rows = [[u.get("start"), u["end"], u["val"], u.get("fy"), u.get("fp"), u.get("form"), u.get("filed"),
                 u.get("accn"), u.get("frame", "")] for u in units if u.get("form") in ("10-Q", "10-K")]
        if rows:
            out[tag] = rows
    return out


# ---------------------------------------------------------------- 공시 본문 파서
_BLOCK = re.compile(r"</(?:p|div|li|tr|h[1-6]|table|section)\s*>|<br\s*/?>", re.I)


def html_to_lines(h: str) -> list[str]:
    h = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", h)
    h = re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", h)
    h = _BLOCK.sub("\n", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = htmllib.unescape(h).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", x).strip() for x in h.split("\n")]
    return [x for x in lines if x]


SECTION_RULES = {
    "10-Q": (r"management.s discussion and analysis", r"quantitative and qualitative disclosures"),
    "10-K": (r"management.s discussion and analysis", r"quantitative and qualitative disclosures"),
}


def extract_mdna(lines: list[str], form: str) -> list[dict]:
    """MD&A 구간을 찾아 단락 목록으로 만든다. 목차의 같은 제목은 본문이 가장 긴 구간을 골라 건너뛴다."""
    start_rx, end_rx = (re.compile(x, re.I) for x in SECTION_RULES.get(form, SECTION_RULES["10-Q"]))
    best = (0, 0, 0)
    for i, ln in enumerate(lines):
        if len(ln) < 120 and start_rx.search(ln):
            j = next((k for k in range(i + 1, len(lines)) if len(lines[k]) < 150 and end_rx.search(lines[k])),
                     len(lines))
            size = sum(len(x) for x in lines[i + 1:j])
            if size > best[0]:
                best = (size, i + 1, j)
    _, a, b = best
    paras, sub = [], "Overview"
    for ln in lines[a:b]:
        if _is_heading(ln):
            sub = ln
            continue
        if len(ln) >= 60 or ln.startswith("•"):
            paras.append({"sub": sub, "text": ln})
    return paras


def _is_heading(ln: str) -> bool:
    return len(ln) < 80 and not ln.endswith((".", ":", ";")) and not re.search(r"\$|\d{2,}%", ln) \
        and ln[:1].isupper() and len(ln.split()) <= 9


_GUIDE = re.compile(r"^(Revenue|Gross margin|Operating expenses|Diluted earnings per share)\b(.*?)(\$[\d.]+\s*billion\s*±\s*\$[\d.]+\s*billion|[\d.]+%\s*±\s*[\d.]+%)", re.I)


def extract_guidance(lines: list[str]) -> list[dict]:
    """보도자료의 Business Outlook(가이던스) 표에서 매출·마진 가이던스 행을 뽑는다."""
    for i, ln in enumerate(lines):
        if re.search(r"business outlook|guidance", ln, re.I) and len(ln) < 120:
            rows = []
            for x in lines[i + 1:i + 30]:
                m = _GUIDE.search(x)
                if m:
                    rows.append([m.group(1), m.group(3)])
            if rows:
                return [{"kind": "guidance", "sub": ln, "rows": rows}]
    return []
