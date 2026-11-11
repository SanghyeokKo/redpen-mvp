"""RedPen REST API (선택 기능) — 사내 시스템 연동용 최소 엔드포인트.

    pip install fastapi uvicorn
    uvicorn api:app --port 8000
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

from redpen.outputs import Ledger
from redpen.workflow import load_context, run

ROOT = Path(__file__).parent
LEDGER_PATH = Path(os.getenv("REDPEN_LEDGER", ROOT / "runs" / "ledger.jsonl"))
CTX = load_context(ROOT / "data", ledger_path=LEDGER_PATH)
app = FastAPI(title="RedPen API", version="0.1.0",
              description="새 공시 1건이 흔드는 기관 내 판단을 찾아 근거와 함께 고치는 AI Agent (MVP)")


@app.get("/events")
def list_events():
    return [{k: e[k] for k in ("id", "label", "company", "role", "fiscal", "filed")} for e in CTX.store.events()]


@app.post("/runs/{event_id}")
def run_event(event_id: str):
    if event_id not in {e["id"] for e in CTX.store.events()}:
        raise HTTPException(404, f"알 수 없는 공시 이벤트: {event_id}")
    r = run(CTX, event_id)
    keep = ("verdict", "rule", "reason", "revision", "evidence_ids")
    return {"run_id": r["run_id"], "event": r["event"]["label"], "node_path": r["node_path"],
            "impacted": r["impacted"], "verdicts": {k: {f: v.get(f) for f in keep} for k, v in r["verdicts"].items()},
            "conflicts": r["conflicts"], "verification": r["verification"],
            "evidence": {k: [{f: e.get(f) for f in ("id", "kind", "accession", "location", "quote", "stance", "url")}
                             for e in v] for k, v in r["evidence"].items()}}


@app.get("/ledger")
def ledger(limit: int = 100):
    led = Ledger(LEDGER_PATH)
    ok, n = led.verify_chain()
    return {"chain_ok": ok, "entries": led.read()[-limit:]}
