"""EDGAR 파서·클라이언트 (네트워크 없이)."""
import io

import pytest

from redpen.edgar import EdgarClient, extract_guidance, extract_mdna, html_to_lines, recent_filings

HTML = """<html><body>
<p>Item 2. Management's Discussion and Analysis of Financial Condition and Results of Operations</p><p>12</p>
<p>Item 3. Quantitative and Qualitative Disclosures About Market Risk</p><p>30</p>
<div>Item 2. Management&#8217;s Discussion and Analysis of Financial Condition and Results of Operations</div>
<div>Results of Operations</div>
<p>Data Center revenue was $10.0 billion, up 50% from a year ago, driven by strong demand for accelerated computing.</p>
<p>Gross margin increased to 70.0% from 65.0% due to a favorable mix of products and higher selling prices.</p>
<div>Item 3. Quantitative and Qualitative Disclosures About Market Risk</div>
<p>We are exposed to interest rate risk and foreign currency risk in the normal course of business operations.</p>
</body></html>"""


def test_mdna_skips_table_of_contents():
    paras = extract_mdna(html_to_lines(HTML), "10-Q")
    assert [p["sub"] for p in paras] == ["Results of Operations"] * 2
    assert paras[0]["text"].startswith("Data Center revenue") and "interest rate" not in " ".join(p["text"] for p in paras)


def test_guidance_rows():
    lines = ["Business Outlook", "Revenue $61.5 billion ± $1.5 billion $61.5 billion ± $1.5 billion",
             "Gross margin 85.95% ± 1.00% 86.25% ± 1.00%"]
    g = extract_guidance(lines)
    assert g[0]["rows"][0] == ["Revenue", "$61.5 billion ± $1.5 billion"]


def test_client_requires_user_agent(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(ValueError):
        EdgarClient()


def test_client_sends_user_agent_and_caches(tmp_path):
    sent = []

    class Resp(io.BytesIO):
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=30):
        sent.append(req.get_header("User-agent"))
        return Resp(b'{"filings": {"recent": {"form": ["10-Q"], "filingDate": ["2026-01-01"], "reportDate": ["2025-12-31"],'
                    b' "accessionNumber": ["1"], "primaryDocument": ["a.htm"], "items": [""]}}}')

    c = EdgarClient("Team test@example.com", cache_dir=tmp_path, opener=opener)
    sub = c.json("https://data.sec.gov/submissions/CIK0000000001.json")
    c.json("https://data.sec.gov/submissions/CIK0000000001.json")
    assert sent == ["Team test@example.com"]  # 두 번째 호출은 캐시
    assert recent_filings(sub)[0]["form"] == "10-Q"
