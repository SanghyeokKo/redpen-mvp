"""근거 탐색: 국문 주장 → 영문 검색어 → BM25로 공시 단락 검색 → 문장 단위 근거·방향 판별."""
from __future__ import annotations

import hashlib
import re

from rank_bm25 import BM25Okapi

from .store import Store, FilingDoc
from .topics import TOPICS, rx_any

STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "for", "on", "by", "our", "we", "as", "at", "is", "was",
        "were", "be", "that", "this", "with", "from", "are", "its", "it", "which", "have", "has", "been"}


def _stem(w: str) -> str:
    """아주 단순한 영문 어간 처리 (increase/increased/increases → increas)."""
    if len(w) <= 4 or w.isdigit():
        return w
    for suf in ("ing", "ed", "es", "s", "e"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def tokenize(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


ABBR = {"U.S", "Inc", "Corp", "Co", "Ltd", "No", "Nos", "vs", "approx", "e.g", "i.e", "Mr", "Ms", "Dr", "St", "Jr",
        "Sr", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec", "Fig", "Ref"}


def split_en_sentences(text: str) -> list[str]:
    """마침표 + 공백 + 대문자에서 나누되, 약어(U.S., Inc. 등)와 이니셜에서는 나누지 않는다."""
    text = text.lstrip("•").strip()
    out, start = [], 0
    for m in re.finditer(r"\.\s+(?=[A-Z“(])", text):
        prev = re.search(r"(\S+)$", text[start:m.start()])
        tok = prev.group(1) if prev else ""
        if tok in ABBR or re.fullmatch(r"[A-Z]", tok) or re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]", tok):
            continue
        out.append(text[start:m.start()].strip())
        start = m.end()
    out.append(text[start:].rstrip(". ").strip())
    return [x for x in out if x]


# 경영진 전망·계획 표현 → '가설' 라벨 (사실 근거와 구분)
FORWARD_RX = re.compile(r"\b(expect|expects|expected|anticipate|anticipates|will|outlook|guidance|believe|believes|"
                        r"plan to|plans to|intend|intends|forecast|projected|projects to|should|estimate|estimates)\b", re.I)


def is_forward_looking(sentence: str, anchors=()) -> bool:
    """근거가 되는 절(clause)에 전망·계획 표현이 있으면 '가설'. 같은 문장의 다른 절은 보지 않는다."""
    if not anchors:
        return bool(FORWARD_RX.search(sentence))
    clauses = re.split(r"[;,]\s+|\s+(?:and|but|while|whereas)\s+", sentence)
    hit = [c for c in clauses if rx_any(anchors, c)] or [sentence]
    return any(FORWARD_RX.search(c) for c in hit)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_query(claim: dict, store: Store, attempt: int) -> list[str]:
    """시도 차수에 따라 검색어를 넓힌다 (1차: 기본, 2차: 동의어 추가, 3차: 회사명·세그먼트 제외한 넓은 검색)."""
    t = TOPICS[claim["topic"]]
    q = list(t.en_query)
    if attempt >= 2:
        q += list(t.en_expand)
    if attempt < 3 and claim["company"] in store.companies:
        q.append(store.companies[claim["company"]]["name"])
    if claim.get("segment"):
        q.append(claim["segment"])
    return q


def corpus_for(claim: dict, event: dict, store: Store, attempt: int) -> list[FilingDoc]:
    """재탐색 범위: 1차 = 새 공시(이벤트), 2차 = + 주장 대상 회사 최신 공시, 3차 = 모니터링 기업 전체."""
    docs = [store.docs[m["doc_key"]] for m in event["members"] if m["in_snapshot"]]
    if attempt >= 2:
        targets = list(store.companies) if claim["company"] == "SECTOR" else [claim["company"]]
        if TOPICS[claim["topic"]].scope == "related" and claim["company"] in store.companies:
            targets += [r["source"] if r["target"] == claim["company"] else r["target"]
                        for r in store.relations if r["type"] == "competitor"
                        and claim["company"] in (r["source"], r["target"])]
        for t in targets:
            docs += [d for d in store.latest_docs(t)]
    if attempt >= 3:
        docs += list(store.docs.values())
    seen, out = set(), []
    for d in docs:
        if d.key not in seen:
            seen.add(d.key)
            out.append(d)
    return out


def _company_ok(claim: dict, doc: FilingDoc, store: Store) -> bool:
    scope = TOPICS[claim["topic"]].scope
    if scope == "market" or claim["company"] == "SECTOR":
        return True
    if doc.company == claim["company"]:
        return True
    if scope == "related":
        return store.relation_type(doc.company, claim["company"]) == "competitor"
    return False


def _period_ok(sentence: str, ctx_years: list[int], doc: FilingDoc) -> bool:
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", sentence)] or ctx_years
    if not years:
        return True
    return max(years) >= int(doc.period[:4])


LINE_ITEM_RX = re.compile(r"(Gross margin|Operating (income|expenses)|Net income|Cash|Inventor)", re.I)
CONTRAST_RX = re.compile(r",?\s+(?:partially offset by|offset by|but|however|whereas|although|despite)\s+", re.I)
HEDGE_RX = re.compile(r"\b(could|might|may)\b", re.I)


def _relevant(t, text: str, segment: str | None) -> bool:
    if not rx_any(t.en_anchor, text):
        return False
    if t.en_terms and not rx_any(t.en_terms, text):
        return False
    return not segment or bool(re.search(rf"\b{re.escape(segment)}\b", text))


def sentence_stance(sentence: str, topic_key: str, segment: str | None = None) -> int | None:
    """근거 문장의 방향: +1/-1, 관련은 있으나 방향 판단 불가 0, 무관 None.

    - '~, partially offset by ~' 처럼 대조 구문이 있으면 토픽과 관련된 쪽 구절만 본다.
    - could·may·might 같은 가능성 표현(리스크 서술)은 방향 근거로 쓰지 않는다 (0).
    """
    t = TOPICS[topic_key]
    if not _relevant(t, sentence, segment):
        return None
    if t.interpretive:
        return 0
    parts = [p for p in CONTRAST_RX.split(sentence) if _relevant(t, p, segment)] or [sentence]
    up = any(rx_any(t.en_up, p) for p in parts)
    down = any(rx_any(t.en_down, p) for p in parts)
    if not (up or down):
        return None
    if up and down or any(HEDGE_RX.search(p) for p in parts):
        return 0
    return 1 if up else -1


def retrieve(claim: dict, event: dict, store: Store, attempt: int, top_k: int = 6) -> tuple[list[dict], dict]:
    docs = corpus_for(claim, event, store, attempt)
    query = build_query(claim, store, attempt)
    t = TOPICS[claim["topic"]]
    units = []  # 문장 단위 색인 (단락 위치는 근거 위치로 보존)
    for d in docs:
        for p in d.paragraphs:
            for s in split_en_sentences(p["text"]):
                units.append((d, p, s))
    trace = {"attempt": attempt, "query": " ".join(query), "corpus": [d.label for d in docs], "hits": 0}
    if not units:
        return [], trace
    bm25 = BM25Okapi([tokenize(u[2]) for u in units])
    scores = bm25.get_scores(tokenize(" ".join(query)))
    # 후보: BM25 상위 문장 중 토픽 표현이 있는 문장. 정렬: BM25 + 주어 위치 가점(문장 앞부분에 지표명) + 방향 표현 가점
    cands = []
    for i in sorted(range(len(units)), key=lambda i: scores[i], reverse=True)[: top_k * 6]:
        if scores[i] <= 0:
            continue
        d, p, s = units[i]
        if not _company_ok(claim, d, store) or not _period_ok(s, p["ctx_years"], d):
            continue
        st = sentence_stance(s, claim["topic"], claim.get("segment"))
        if st is None:
            continue
        lead = rx_any(t.en_anchor, s[:40])
        other = claim["topic"] != "gross_margin" and bool(LINE_ITEM_RX.match(s))  # 다른 재무 항목이 주어인 문장
        cands.append((scores[i] + 3.0 * lead - 3.0 * other + 1.0 * (st != 0), i, st))
    found, per_doc, per_para = [], {}, {}
    for rank, i, st in sorted(cands, reverse=True):
        d, p, s = units[i]
        pk = (d.key, p["idx"])
        if per_doc.get(d.key, 0) >= 3 or per_para.get(pk, 0) >= 2:
            continue
        per_doc[d.key] = per_doc.get(d.key, 0) + 1
        per_para[pk] = per_para.get(pk, 0) + 1
        stance = 0 if st == 0 else (1 if st == claim["direction"] else -1) if claim["direction"] else 0
        found.append({
            "kind": "text", "company": d.company, "accession": d.accession, "form": d.form, "filed": d.filed,
            "url": d.url, "doc_key": d.key, "paragraph_index": p["idx"],
            "location": f"{d.form} {d.section.split('.')[0]} › {p['sub']} ¶{p['idx'] + 1}",
            "quote": s, "direction": st, "stance": stance,
            "forward_looking": is_forward_looking(s, t.en_anchor),
            "score": round(float(rank), 2), "attempt": attempt, "sha256": sha(s),
        })
    trace["hits"] = len(found)
    return found, trace


def guidance_evidence(claim: dict, event: dict, store: Store) -> list[dict]:
    """가이던스 주장: 이벤트에 포함된 보도자료의 Business Outlook 표를 근거로 쓴다."""
    out = []
    for m in event["members"]:
        if not m["in_snapshot"]:
            continue
        d = store.docs[m["doc_key"]]
        if d.company != claim["company"]:
            continue
        for tb in d.tables:
            if tb.get("kind") != "guidance":
                continue
            rev = next((r for r in tb["rows"] if r[0].lower() == "revenue"), None)
            if not rev:
                continue
            quote = f"{tb['sub']}: Revenue {rev[1]} (GAAP)"
            out.append({"kind": "guidance", "company": d.company, "accession": d.accession, "form": d.form,
                        "filed": d.filed, "url": d.url, "doc_key": d.key, "paragraph_index": -1,
                        "location": f"{d.form} {d.section} › {tb['sub']}", "quote": quote, "table": tb,
                        "direction": 0, "stance": 0, "forward_looking": True, "attempt": 1, "sha256": sha(quote)})
    return out
