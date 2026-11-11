"""규칙·수치·워크플로 회귀 테스트 (정답셋 초안 기준)."""
from conftest import AMD, DATA, MU, NVDA

from redpen import xbrl
from redpen.eval import evaluate, load_gold
from redpen.workflow import run


def test_claim_extraction(ctx):
    c = ctx.claims
    assert (c["A1"]["topic"], c["A1"]["direction"]) == ("memory_balance", 1)
    assert (c["S2"]["topic"], c["S2"]["direction"]) == ("memory_balance", -1)
    assert (c["S3"]["company"], c["S3"]["topic"], c["S3"]["direction"]) == ("AMD", "competition", -1)
    assert c["B4"]["numeric"]["op"] == "lt" and c["B4"]["numeric"]["hi"] == 50
    assert c["C1"]["numeric"]["op"] == "range" and c["C1"]["topic"] == "gross_margin"
    assert c["A2"]["modality"] == "forecast" and c["C1"]["modality"] == "fact"
    assert all(c[x]["topic"] is None for x in ("A5", "B5", "C5", "S4"))


def test_xbrl_matches_filings(ctx):
    s = ctx.store
    nv = xbrl.gross_margin(s, "NVDA")
    assert (nv["value"], nv["prev_value"], nv["change_pp"]) == (75.0, 72.4, 2.6)   # 10-Q: 75.0% vs 72.4%
    assert xbrl.gross_margin(s, "AMD")["value"] == 53.9                           # 10-Q: 54% (반올림)
    mu = xbrl.revenue_growth(s, "MU")
    assert mu["derived"] and round(mu["value"] / 1e6) == 54229                     # 보도자료: $54.23B
    assert xbrl.gross_margin(s, "MU")["annual_value"] == 80.7                      # 10-K: 81% (반올림)


def test_gold_events(ctx):
    gold = load_gold(DATA / "gold.yaml")
    for eid in (MU, AMD):
        r = run(ctx, eid, record=False)
        ev = evaluate(r, gold)
        assert ev["verdict_accuracy"] == 1.0, ev["mismatches"]
        assert ev["impact_recall"] == 1.0 and ev["no_impact_fp_rate"] == 0.0
        assert ev["conflict_recall"] == 1.0 and ev["conflict_precision"] == 1.0
        assert ev["numeric_errors"] == 0 and r["verification"]["passed"]


def test_research_loop(ctx, mu_result):
    r = mu_result
    # A3: 1차(마이크론 공시)에서 XBRL 반대 근거 1건 → 2차에 엔비디아 10-Q로 범위 확장 → 독립 근거 2건 → 수정
    assert r["attempts"]["A3"] == 2 and r["verdicts"]["A3"]["verdict"] == "수정"
    assert r["node_path"].count("retriever") == 2
    amd = run(ctx, AMD, record=False)
    # B3: 반대 근거가 한 공시에서만 나옴 → 3회 재탐색 후에도 독립 근거 1건 → 확인 필요
    assert amd["attempts"]["B3"] == 3 and amd["verdicts"]["B3"]["rule"] == "R4a"


def test_every_judgment_has_evidence(mu_result):
    r = mu_result
    for cid in r["impacted"]:
        v = r["verdicts"][cid]
        if v["verdict"] in ("수정", "유지"):
            assert v["evidence_ids"], cid
        if v["verdict"] == "수정":
            assert len(v["contra_fact_groups"]) >= 2, cid


def test_revision_numbers_come_from_calculator(mu_result):
    rev = mu_result["verdicts"]["C1"]["revision"]
    assert "86.8%" in rev and "44.7%" in rev and "42.1%p" in rev


def test_conflicts(mu_result):
    pairs = {frozenset(p) for g in mu_result["conflicts"] for p in g["pairs"]}
    assert pairs == {frozenset(("S1", "B2")), frozenset(("S2", "C2")), frozenset(("S2", "A1"))}
    k = next(g for g in mu_result["conflicts"] if g["topic"] == "memory_balance")
    assert k["favored"] == 1  # 최신 공시는 '공급 부족(타이트)' 쪽


def test_nvda_event_runs(ctx):
    r = run(ctx, NVDA, record=False)
    assert r["verification"]["passed"] and r["verdicts"]["A3"]["verdict"] == "수정"
