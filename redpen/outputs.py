"""결과물 작성 (Writer): 변경 추적 docx 빨간펜 수정안 + 판단 이력 원장(append-only, 해시 체인)."""
from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

AUTHOR = "RedPen"
VERDICT_ORDER = ["수정", "확인 필요", "유지", "영향 없음"]
LABEL3 = {"text": "사실", "xbrl": "계산값", "calc": "계산값", "guidance": "가설"}


def label3(ev: dict) -> str:
    """기획서의 3단계 라벨: 사실 / 계산값 / 가설."""
    if ev["kind"] == "text" and ev.get("forward_looking"):
        return "가설"
    return LABEL3.get(ev["kind"], "사실")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ================================================================ 판단 이력 원장
class Ledger:
    """append-only JSONL. 각 항목은 직전 항목의 해시를 포함해 사후 변경을 탐지할 수 있다."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def _last_hash(self) -> str:
        rows = self.read()
        return rows[-1]["hash"] if rows else "GENESIS"

    def append(self, entries: list[dict]) -> list[dict]:
        prev, out = self._last_hash(), []
        with self.path.open("a", encoding="utf-8") as f:
            for e in entries:
                e = {**e, "ts": e.get("ts") or now_iso(), "prev_hash": prev}
                e["hash"] = _hash(e)
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
                prev = e["hash"]
                out.append(e)
        return out

    def verify_chain(self) -> tuple[bool, int]:
        prev = "GENESIS"
        rows = self.read()
        for i, e in enumerate(rows):
            body = {k: v for k, v in e.items() if k != "hash"}
            if e["prev_hash"] != prev or _hash(body) != e["hash"]:
                return False, i
            prev = e["hash"]
        return True, len(rows)


def _hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def evidence_snapshot(ev: dict) -> dict:
    snap = {"id": ev["id"], "kind": ev["kind"], "label": label3(ev), "company": ev["company"],
            "accession": ev["accession"], "form": ev["form"], "filed": ev["filed"], "location": ev["location"],
            "url": ev["url"], "stance": ev["stance"], "sha256": ev["sha256"]}
    if ev.get("quote"):
        snap["quote"] = ev["quote"]
    if ev.get("metric"):
        m = ev["metric"]
        snap["metric"] = {k: m[k] for k in ("metric", "period", "value", "unit", "formula", "prev_value",
                                             "change_pp", "yoy_pct", "annual_value", "derived") if k in m}
    return snap


def run_entries(result: dict) -> list[dict]:
    """한 번의 실행 결과를 원장 항목으로 변환 (영향 문장마다 판정 + 근거 스냅샷)."""
    ev = result["event"]
    base = {"run_id": result["run_id"], "event_id": ev["id"], "engine": result["engine"]}
    counts = {k: 0 for k in VERDICT_ORDER}
    for v in result["verdicts"].values():
        counts[v["verdict"]] += 1
    out = [{**base, "type": "run", "event": ev["label"], "verdict_counts": counts,
            "conflicts": len(result["conflicts"]), "verification": {
                k: result["verification"][k] for k in ("quotes_checked", "numbers_checked",
                                                       "revision_numbers_checked", "passed")},
            "node_path": result.get("node_path", [])}]
    for cid in result["impacted"]:
        v, c = result["verdicts"][cid], result["claims"][cid]
        evs = [e for e in result["evidence"].get(cid, []) if e["id"] in v["evidence_ids"]]
        out.append({**base, "type": "judgment", "claim_id": cid, "doc_id": c["doc_id"], "sentence": c["text"],
                    "verdict": v["verdict"], "rule": v["rule"], "reason": v["reason"],
                    "revision": v.get("revision"), "attempts": result["attempts"].get(cid, 0),
                    "path": v.get("path", {}).get("text"), "evidence": [evidence_snapshot(e) for e in evs]})
    for g in result["conflicts"]:
        out.append({**base, "type": "conflict", "conflict_id": g["id"], "topic": g["label"], "pairs": g["pairs"],
                    "teams": g["teams"], "evidence_plus": g["evidence_plus"], "evidence_minus": g["evidence_minus"]})
    return out


def decision_entry(result: dict, claim_id: str, decision: str, approver: str, note: str = "") -> dict:
    v, c = result["verdicts"][claim_id], result["claims"][claim_id]
    return {"run_id": result["run_id"], "event_id": result["event"]["id"], "engine": result["engine"],
            "type": "approval" if decision == "approved" else "rejection", "claim_id": claim_id,
            "doc_id": c["doc_id"], "sentence": c["text"], "verdict": v["verdict"], "revision": v.get("revision"),
            "approver": approver, "note": note}


# ================================================================ 빨간펜 수정안 (변경 추적 docx)
def _set_korean_font(doc: Document, name: str = "Malgun Gothic"):
    st = doc.styles["Normal"]
    st.font.name = name
    st.font.size = Pt(10.5)
    rpr = st.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    for k in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(k), name)


def _rev_run(text: str, deleted: bool) -> OxmlElement:
    r = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "C62828")
    rpr.append(color)
    r.append(rpr)
    t = OxmlElement("w:delText" if deleted else "w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    r.append(t)
    return r


def _tracked(paragraph, old: str, new: str, rev_id: int, date: str) -> int:
    for tag, text, deleted in (("w:del", old, True), ("w:ins", new, False)):
        el = OxmlElement(tag)
        el.set(qn("w:id"), str(rev_id))
        el.set(qn("w:author"), AUTHOR)
        el.set(qn("w:date"), date)
        el.append(_rev_run(text, deleted))
        paragraph._p.append(el)
        rev_id += 1
    return rev_id


def _comment_text(cid: str, v: dict, evidence: list[dict], conflicts_of: list[dict]) -> str:
    lines = [f"[{cid}] {v['verdict']} ({v['rule']}) — {v['reason']}"]
    if v.get("path"):
        lines.append(f"영향 경로: {v['path']['text']}")
    for e in evidence:
        if e["id"] not in v["evidence_ids"] or e["stance"] == 0 and v["verdict"] != "확인 필요":
            continue
        tag = {1: "같은 방향", -1: "반대", 0: "참고"}[e["stance"]]
        if e.get("metric"):
            m = e["metric"]
            lines.append(f"{e['id']} [{label3(e)}·{tag}] {e['location']} — {e.get('basis', '')} · 계산식 {m['formula']}")
        else:
            q = e["quote"] if len(e["quote"]) <= 220 else e["quote"][:217] + "…"
            lines.append(f"{e['id']} [{label3(e)}·{tag}] {e['location']} — “{q}”")
    for g in conflicts_of:
        lines.append(f"충돌 {g['id']}: {g['label']} — " + ", ".join("↔".join(p) for p in g["pairs"]))
    return "\n".join(lines)


def build_redline(result: dict, docs: list, decisions: dict | None = None) -> bytes:
    """내부 문서 전체를 다시 쓰되, '수정' 판정 문장은 변경 추적(삭제·삽입)으로, 나머지 영향 문장은 메모로 표시."""
    decisions = decisions or {}
    ev = result["event"]
    verdicts, claims, evidence = result["verdicts"], result["claims"], result["evidence"]
    conflicts = result["conflicts"]
    d = Document()
    _set_korean_font(d)
    date = now_iso().replace("+00:00", "Z")
    d.add_heading("RedPen 빨간펜 수정안", level=0)
    p = d.add_paragraph()
    p.add_run(f"새 공시: {ev['label']}\n").bold = True
    p.add_run(f"실행 {result['run_id']} · 엔진 {result['engine_label']} · 생성 {date}\n")
    p.add_run("수정 판정은 변경 내용 추적(삭제·삽입)으로, 유지·확인 필요는 메모로 표시했습니다. "
              "Word의 [검토] 탭에서 수락/거부할 수 있으며, 원본 문서는 자동으로 덮어쓰지 않습니다. "
              "수정안은 사실 전제만 고치며 결론·투자의견은 담당자가 판단합니다.").italic = True
    counts = {k: sum(1 for v in verdicts.values() if v["verdict"] == k) for k in VERDICT_ORDER}
    d.add_paragraph(" · ".join(f"{k} {n}" for k, n in counts.items()) + f" · 충돌 {len(conflicts)}건")
    rev_id = 1000
    for doc in docs:
        d.add_heading(f"{doc.title}", level=1)
        meta = d.add_paragraph()
        r = meta.add_run(f"{doc.team} · {doc.author} · {doc.date}")
        r.italic = True
        r.font.color.rgb = RGBColor(0x59, 0x65, 0x7A)
        body = Path(doc.path).read_text(encoding="utf-8").split("---", 2)[2]
        n = 0
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith(">"):
                q = d.add_paragraph()
                rq = q.add_run(line.lstrip("> ").strip())
                rq.italic = True
                rq.font.size = Pt(9)
                rq.font.color.rgb = RGBColor(0x8A, 0x94, 0xA6)
                continue
            if line.startswith("#"):
                d.add_heading(line.lstrip("#").strip(), level=2)
                continue
            para = d.add_paragraph()
            for s in [x.strip() for x in re.split(r"(?<=다\.)\s+", line) if x.strip()]:
                n += 1
                cid = f"{doc.id}{n}"
                v = verdicts.get(cid)
                conf = [g for g in conflicts if cid in g["plus"] + g["minus"]]
                if not v or (v["verdict"] == "영향 없음" and not conf):
                    para.add_run(s + " ")
                    continue
                tag = para.add_run(f"[{cid}] ")
                tag.font.size = Pt(8)
                tag.font.color.rgb = RGBColor(0x59, 0x65, 0x7A)
                decision = decisions.get(cid, "pending")
                if v["verdict"] == "수정" and v.get("revision") and decision != "rejected":
                    rev_id = _tracked(para, s, v["revision"], rev_id, date)
                    para.add_run(" ")
                else:
                    body_run = para.add_run(s)
                    if v["verdict"] == "확인 필요":
                        body_run.font.highlight_color = WD_COLOR_INDEX.YELLOW
                    para.add_run(" ")
                note = _comment_text(cid, v, evidence.get(cid, []), conf)
                if v["verdict"] == "수정":
                    note += f"\n승인 상태: {_DECISION_KO[decision]}"
                d.add_comment(tag, text=note, author=AUTHOR, initials="RP")
    if conflicts:
        d.add_heading("팀 간 충돌 리포트", level=1)
        for g in conflicts:
            d.add_heading(f"{g['id']} · {g['label']} — {' vs '.join(g['teams'])}", level=2)
            t = d.add_table(rows=1, cols=3)
            t.style = "Light Grid Accent 1"
            hdr = t.rows[0].cells
            hdr[0].text, hdr[1].text, hdr[2].text = "문장", "방향", "판정"
            for cid in g["plus"] + g["minus"]:
                c = claims[cid]
                row = t.add_row().cells
                row[0].text = f"[{cid}] {c['text']}"
                row[1].text = g["dir_labels"][0] if c["direction"] > 0 else g["dir_labels"][1]
                row[2].text = verdicts[cid]["verdict"]
            fav = {1: g["dir_labels"][0], -1: g["dir_labels"][1], 0: "판단 보류"}[g["favored"]]
            d.add_paragraph(f"최신 공시 근거: '{g['dir_labels'][0]}' {g['evidence_plus']}건 · "
                            f"'{g['dir_labels'][1]}' {g['evidence_minus']}건 → '{fav}' 쪽 근거가 많음. "
                            "어느 판단을 따를지는 양쪽 담당자가 결정합니다.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


_DECISION_KO = {"pending": "검토 대기", "approved": "승인", "rejected": "반려"}
