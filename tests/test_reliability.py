"""검증 루프·LLM 대체·원장·변경 추적 문서."""
import io
import json
import zipfile

from conftest import DATA, MU

import redpen.workflow as wf
from redpen.llm import LLMClient, parse_json
from redpen.outputs import Ledger, build_redline, run_entries


def test_verifier_catches_tampered_quote_and_rejudges(monkeypatch):
    orig = wf.retrieve

    def tampered(claim, event, store, attempt, top_k=6):
        found, trace = orig(claim, event, store, attempt, top_k)
        if claim["sentence_id"] == "C2" and found:
            found[0] = {**found[0], "quote": found[0]["quote"].replace("outpacing", "lagging")}
        return found, trace

    monkeypatch.setattr(wf, "retrieve", tampered)
    r = wf.run(wf.load_context(DATA), MU, record=False)
    i = r["node_path"].index("verifier_writer")
    assert r["node_path"][i + 1] == "judge"            # 검증 실패 → 재판정
    assert r["verification"]["passed"]                  # 오염 근거를 빼고 다시 판정해 통과
    assert any(x.startswith("C2-") for x in r["excluded"])
    assert r["verdicts"]["C2"]["verdict"] == "유지"     # 남은 독립 근거로 판정 유지


class FakeLLM:
    """추출·분류는 실패(→ 규칙 대체), 수정 문안은 틀린 숫자를 쓰는 LLM."""
    label = "fake · test"

    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt, role="judge"):
        self.calls += 1
        if role == "extract":
            raise ValueError("bad json")
        return {"revision": "매출총이익률은 99.9%로 크게 올랐다."}


def test_llm_fallbacks_and_number_guard():
    llm = FakeLLM()
    ctx = wf.load_context(DATA, llm=llm)
    assert all(c["extractor"].startswith("rules") for c in ctx.claims.values())
    r = wf.run(ctx, MU, record=False)
    assert r["verification"]["passed"]
    assert "judge" in r["node_path"][r["node_path"].index("verifier_writer") + 1:]   # 수정안 숫자 불일치 → 재판정
    for cid in ("A3", "B4", "C1"):
        v = r["verdicts"][cid]
        assert v["revision_source"] == "template" and "99.9" not in v["revision"]


def test_llm_client_parsing():
    seen = {}

    def transport(url, body, headers):
        seen["url"], seen["model"] = url, body["model"]
        return {"content": [{"type": "text", "text": '```json\n{"stance": -1}\n```'}]}

    c = LLMClient("anthropic", "k", "big-model", "small-model", transport=transport)
    assert c.complete_json("x", role="extract") == {"stance": -1}
    assert seen["model"] == "small-model" and seen["url"].endswith("/v1/messages")
    assert parse_json('설명 {"a": 1} 끝') == {"a": 1}


def test_ledger_hash_chain(tmp_path, mu_result):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(run_entries(mu_result))
    ok, n = led.verify_chain()
    assert ok and n == 1 + len(mu_result["impacted"]) + len(mu_result["conflicts"])
    lines = (tmp_path / "l.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[3])
    row["verdict"] = "유지"  # 사후 변조
    lines[3] = json.dumps(row, ensure_ascii=False)
    (tmp_path / "l.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert led.verify_chain() == (False, 3)


def test_redline_docx_has_tracked_changes(ctx, mu_result):
    data = build_redline(mu_result, ctx.docs, {"B4": "rejected"})
    z = zipfile.ZipFile(io.BytesIO(data))
    xml = z.read("word/document.xml").decode("utf-8")
    assert xml.count("<w:del ") == 3 and xml.count("<w:ins ") == 3   # 수정 4건 중 1건 반려
    assert "word/comments.xml" in z.namelist()


def test_llm_call_cap(monkeypatch):
    monkeypatch.setenv("REDPEN_LLM_MAX_CALLS", "2")
    c = LLMClient("anthropic", "k", "m", "f", transport=lambda u, b, h: {"content": [{"text": '{"ok": 1}'}]})
    assert c.complete_json("a") == {"ok": 1} and c.complete_json("b") == {"ok": 1}
    try:
        c.complete_json("c")
        raise AssertionError("상한을 넘었는데 호출됨")
    except RuntimeError:
        pass
