"""내부 문서 로딩, 문장 분리, 주장 추출(규칙 기반 / LLM)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml

from .topics import TOPICS, PRIORITY, SEGMENTS, FORECAST_MARKERS


@dataclass
class InternalDoc:
    id: str
    title: str
    team: str
    author: str
    date: str
    company: str
    path: str
    sentences: list = field(default_factory=list)  # list[dict]


def load_docs(doc_dir: str | Path) -> list[InternalDoc]:
    docs = []
    for p in sorted(Path(doc_dir).glob("*.md")):
        raw = p.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
        if not m:
            continue
        meta = yaml.safe_load(m.group(1))
        doc = InternalDoc(id=str(meta["id"]), title=meta["title"], team=meta["team"], author=meta.get("author", ""),
                          date=str(meta["date"]), company=meta["company"], path=str(p))
        doc.sentences = split_sentences(doc.id, m.group(2))
        docs.append(doc)
    return docs


def split_sentences(doc_id: str, body: str) -> list[dict]:
    """줄 단위로 읽고, 한 줄에 여러 문장이 있으면 '다.' 기준으로 나눈다. 인용문(>)·제목(#)은 제외."""
    out, section, n = [], "", 0
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(">"):
            continue
        if line.startswith("#"):
            section = line.lstrip("#").strip()
            continue
        parts = re.split(r"(?<=다\.)\s+", line)
        for s in parts:
            s = s.strip()
            if not s:
                continue
            n += 1
            out.append({"id": f"{doc_id}{n}", "doc_id": doc_id, "text": s, "section": section, "order": n})
    return out


# ---------------------------------------------------------------- 규칙 기반 추출
def _after(text: str, anchors) -> str:
    """토픽 키워드 뒤쪽 문맥 (방향 판단은 주어 뒤의 서술부에서 한다)."""
    pos = [text.find(a) for a in anchors if a in text]
    return text[min(pos):] if pos else text


def detect_topics(text: str) -> list[str]:
    found = []
    probe = text.replace("매출총이익", "총이익")  # '매출총이익률'을 매출 성장 주장으로 오인하지 않도록
    for key in PRIORITY:
        t = TOPICS[key]
        src = probe if key == "revenue_growth" else text
        if any(k in src for k in t.ko_any) and (not t.ko_also or any(k in src for k in t.ko_also)):
            found.append(key)
    return found


LIMITERS = ("제한적", "어렵", "않", "못하")


def detect_direction(text: str, topic_key: str) -> int:
    t = TOPICS[topic_key]
    tail = _after(text, t.ko_any)
    up = [tail.find(w) for w in t.ko_up if w in tail]
    down = [tail.find(w) for w in t.ko_down if w in tail]
    if up and any(lim in tail[min(up):] for lim in LIMITERS):
        return -1  # '확대는 제한적' 처럼 증가 표현을 부정하는 서술
    if up and not down:
        return 1
    if down and not up:
        return -1
    if up and down:  # 서술부에서 먼저 나온 쪽
        return 1 if min(up) < min(down) else -1
    return 0


def detect_company(text: str, default: str, companies: dict) -> str:
    for ticker, c in companies.items():
        if any(a in text for a in c.get("aliases", [])):
            return ticker
    return default


def detect_numeric(text: str) -> dict | None:
    m = re.search(r"(\d+(?:\.\d+)?)%대", text)
    if m:
        lo = float(m.group(1))
        return {"op": "range", "lo": lo, "hi": lo + 10, "raw": m.group(0)}
    m = re.search(r"(\d+(?:\.\d+)?)%를?\s*(밑돌|하회|넘지 못)", text)
    if m:
        return {"op": "lt", "hi": float(m.group(1)), "raw": m.group(0)}
    m = re.search(r"(\d+(?:\.\d+)?)%를?\s*(웃돌|상회|넘)", text)
    if m:
        return {"op": "gt", "lo": float(m.group(1)), "raw": m.group(0)}
    m = re.search(r"(\d+(?:\.\d+)?)%\s*수준", text)
    if m:
        v = float(m.group(1))
        return {"op": "approx", "lo": v - 5, "hi": v + 5, "raw": m.group(0)}
    return None


def extract_claim_rules(sent: dict, doc: InternalDoc, companies: dict) -> dict:
    text = sent["text"]
    topics = detect_topics(text)
    topic = topics[0] if topics else None
    segment = None
    if topic == "revenue_growth":
        for ko, en in SEGMENTS.items():
            if ko in text:
                segment = en
    return {
        "sentence_id": sent["id"], "doc_id": doc.id, "text": text, "section": sent["section"],
        "company": detect_company(text, doc.company, companies),
        "topic": topic, "topics": topics,
        "claim_type": TOPICS[topic].claim_type if topic else None,
        "direction": detect_direction(text, topic) if topic else 0,
        "modality": "forecast" if any(m in text for m in FORECAST_MARKERS) else "fact",
        "numeric": detect_numeric(text),
        "segment": segment,
        "extractor": "rules",
    }


# ---------------------------------------------------------------- LLM 추출 (선택)
LLM_SCHEMA_HINT = (
    '{"topic": one of ' + str(list(TOPICS)) + ' or null, "direction": 1|-1|0, '
    '"modality": "fact"|"forecast", "company": "NVDA"|"AMD"|"MU"|"SECTOR", "segment": string|null}'
)


def extract_claim_llm(sent: dict, doc: InternalDoc, companies: dict, llm) -> dict:
    """LLM으로 주장을 구조화한다. 형식이 맞지 않으면 규칙 기반 결과로 되돌린다."""
    base = extract_claim_rules(sent, doc, companies)
    topic_desc = "\n".join(f"- {k}: {t.label} ({t.claim_type})" for k, t in TOPICS.items())
    prompt = (
        "다음은 기관 내부 투자 문서의 한 문장입니다. 이 문장이 주장하는 사실 전제를 구조화하세요.\n"
        f"문서: {doc.title} (기본 대상: {doc.company})\n문장: {sent['text']}\n\n"
        f"토픽 후보:\n{topic_desc}\n\nJSON 형식으로만 답하세요: {LLM_SCHEMA_HINT}\n"
        "direction은 해당 토픽이 증가·가속·타이트(+1)인지 감소·둔화·완화(-1)인지, 판단 불가(0)인지입니다."
    )
    try:
        out = llm.complete_json(prompt, role="extract")
        topic = out.get("topic")
        if topic is not None and topic not in TOPICS:
            raise ValueError(f"unknown topic {topic}")
        base.update({
            "topic": topic,
            "claim_type": TOPICS[topic].claim_type if topic else None,
            "direction": int(out.get("direction", 0)),
            "modality": out.get("modality", base["modality"]),
            "company": out.get("company", base["company"]),
            "segment": out.get("segment"),
            "extractor": "llm",
        })
    except Exception as e:  # noqa: BLE001 - LLM 실패 시 규칙 결과 유지
        base["extractor"] = f"rules (LLM 실패: {type(e).__name__})"
    return base


def extract_all(docs: list[InternalDoc], companies: dict, llm=None) -> dict:
    claims = {}
    for d in docs:
        for s in d.sentences:
            c = extract_claim_llm(s, d, companies, llm) if llm else extract_claim_rules(s, d, companies)
            claims[c["sentence_id"]] = c
    return claims
