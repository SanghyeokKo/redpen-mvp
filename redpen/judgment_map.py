"""판단 연결 지도: 문장–지표(토픽)–기업–문서 그래프 (NetworkX).

새 공시가 들어오면, 공시 회사에서 출발해 기업 관계(공급사·고객·경쟁사)와 토픽 전파 규칙을 따라
흔들리는 문장을 찾는다.
"""
from __future__ import annotations

import networkx as nx

from .retrieval import split_en_sentences, sentence_stance, _period_ok
from .store import Store
from .topics import TOPICS, PROPAGATION, SAME_COMPANY_EXTRA

REL_LABEL = {"same": "같은 회사", "supplier_to_customer": "공급사 → 고객사", "customer_to_supplier": "고객사 → 공급사",
             "competitor": "경쟁사", "sector": "섹터 문서"}


def touched_topics(event: dict, store: Store) -> dict[str, dict]:
    """새 공시(이벤트)가 건드린 토픽: 공시 문장에서 토픽 근거가 발견되거나, XBRL·가이던스 표가 있는 토픽."""
    found: dict[str, dict] = {}
    for m in event["members"]:
        if not m["in_snapshot"]:
            continue
        d = store.docs[m["doc_key"]]
        for p in d.paragraphs:
            for s in split_en_sentences(p["text"]):
                if not _period_ok(s, p["ctx_years"], d):
                    continue
                for key in TOPICS:
                    if key == "guidance":
                        continue
                    st = sentence_stance(s, key)
                    if st is not None:
                        f = found.setdefault(key, {"count": 0, "example": s, "source": d.label})
                        f["count"] += 1
        for tb in d.tables:
            if tb.get("kind") == "guidance":
                found.setdefault("guidance", {"count": 1, "example": f"{tb['sub']} 표", "source": d.label})
    t = event["company"]
    if store.facts(t, "GrossProfit"):
        found.setdefault("gross_margin", {"count": 0, "example": "XBRL GrossProfit", "source": "XBRL"})
        found["gross_margin"]["xbrl"] = True
    if store.facts(t, store.revenue_tag(t)):
        found.setdefault("revenue_growth", {"count": 0, "example": "XBRL 매출", "source": "XBRL"})
        found["revenue_growth"]["xbrl"] = True
    return found


class JudgmentMap:
    def __init__(self, store: Store, docs: list, claims: dict):
        self.store, self.docs, self.claims = store, docs, claims
        G = nx.DiGraph()
        for t, c in store.companies.items():
            G.add_node(("company", t), label=c["name_ko"], role=c["role"])
        G.add_node(("company", "SECTOR"), label="섹터 전체", role="섹터")
        for r in store.relations:
            G.add_edge(("company", r["source"]), ("company", r["target"]), rel=r["type"], note=r.get("note", ""))
        for d in docs:
            G.add_node(("doc", d.id), label=d.title, team=d.team, date=d.date)
            for s in d.sentences:
                c = claims[s["id"]]
                G.add_node(("sent", s["id"]), label=s["text"], topic=c["topic"])
                G.add_edge(("doc", d.id), ("sent", s["id"]), rel="contains")
                G.add_edge(("sent", s["id"]), ("company", c["company"]), rel="about")
                if c["topic"]:
                    G.add_node(("topic", c["topic"]), label=TOPICS[c["topic"]].label)
                    G.add_edge(("sent", s["id"]), ("topic", c["topic"]), rel="asserts")
        self.G = G

    def stats(self) -> dict:
        n = lambda kind: sum(1 for x in self.G.nodes if x[0] == kind)  # noqa: E731
        return {"docs": n("doc"), "sentences": n("sent"), "topics": n("topic"), "companies": n("company"),
                "linked": sum(1 for c in self.claims.values() if c["topic"]), "edges": self.G.number_of_edges()}

    def impacted(self, event: dict, touched: dict) -> tuple[list[str], dict[str, dict]]:
        src = event["company"]
        hits, paths = [], {}
        for sid, c in self.claims.items():
            if not c["topic"]:
                continue
            dst = c["company"]
            if dst == "SECTOR":
                rel, targets = "sector", {k: [k] for k in touched}
            else:
                rel = self.store.relation_type(src, dst)
                if rel is None:
                    continue
                if rel == "same":
                    targets = {k: [k] + SAME_COMPANY_EXTRA.get(k, []) for k in touched}
                else:
                    targets = {k: v for k, v in PROPAGATION[rel].items() if k in touched}
            via = [k for k, outs in targets.items() if c["topic"] in outs]
            if via:
                hits.append(sid)
                paths[sid] = {"relation": rel, "relation_label": REL_LABEL[rel], "via_topics": via,
                              "from": src, "to": dst,
                              "text": _path_text(self.store.name_ko(src), self.store.name_ko(dst), rel,
                                                 " / ".join(f"{TOPICS[v].label} → {TOPICS[c['topic']].label}"
                                                            if v != c["topic"] else TOPICS[v].label for v in via))}
        return hits, paths

    def to_dot(self, event: dict, verdicts: dict, colors: dict, conflicts: list | None = None) -> str:
        """영향 문장 지도 (Graphviz DOT): 새 공시 → 기업(관계) → 문서(문장 표). 충돌 문장 쌍은 보라 점선."""
        src = event["company"]
        font = "Pretendard, Noto Sans KR, Noto Sans CJK KR, Apple SD Gothic Neo, Malgun Gothic, sans-serif"
        out = ["digraph G {", 'rankdir=LR; bgcolor="transparent"; nodesep=0.22; ranksep=0.55; pad=0.05;',
               f'node [fontname="{font}" fontsize="10.5" shape=box style="rounded,filled" color="#C9D2E0" '
               'fillcolor="#FFFFFF" penwidth="1" margin="0.12,0.06"];',
               f'edge [fontname="{font}" color="#A7B1C2" arrowsize="0.55" fontsize="9.5" fontcolor="#59657A"];']
        role = self.store.companies.get(src, {}).get("role", "")
        out.append(f'ev [label="새 공시\\n{_esc(event["name_ko"])}({role})\\n{_esc(event["fiscal"])}" '
                   f'fillcolor="#0B2545" fontcolor="white" color="#0B2545" fontsize=11];')
        in_conflict = {x for g in (conflicts or []) for p in g["pairs"] for x in p}
        hit_docs = {self.claims[s]["doc_id"] for s, v in verdicts.items() if v["verdict"] != "영향 없음"}
        for d in self.docs:
            t = d.company
            rel = self._rel(src, t)
            linked = d.id in hit_docs
            r = self.store.companies.get(t, {}).get("role", "섹터")
            out.append(f'"co_{t}" [label="{_esc(self.store.name_ko(t))}\\n({r})" '
                       + ('fillcolor="#E8EEF7" color="#8DA2C0"' if linked else 'fillcolor="#F7F8FA" color="#E1E6EE" fontcolor="#9AA3B2"')
                       + "];")
            if linked:
                out.append(f'ev -> "co_{t}" [label=" {_esc(REL_LABEL.get(rel, ""))} "];')
            rows = [f'<TR><TD ALIGN="LEFT" CELLPADDING="5"><B>{_h(d.title)}</B>'
                    f'<FONT COLOR="#59657A" POINT-SIZE="9.5"> · {_h(d.team)} · {_h(d.date)}</FONT></TD></TR>']
            for sent in d.sentences:
                sid = sent["id"]
                v = verdicts.get(sid, {"verdict": "영향 없음"})["verdict"]
                line, fill = colors[v]
                text = sent["text"] if len(sent["text"]) <= 30 else sent["text"][:29] + "…"
                if v == "영향 없음":
                    rows.append(f'<TR><TD PORT="{sid}" ALIGN="LEFT" BGCOLOR="#FFFFFF"><FONT COLOR="#A3ACBA">'
                                f'{sid}  {_h(text)}</FONT></TD></TR>')
                else:
                    mark = " ⇄" if sid in in_conflict else ""
                    rows.append(f'<TR><TD PORT="{sid}" ALIGN="LEFT" BGCOLOR="{fill}"><FONT COLOR="{line}"><B>{sid} {_h(v)}'
                                f'{mark}</B></FONT>  <FONT COLOR="#1F2733">{_h(text)}</FONT></TD></TR>')
            border = "#8DA2C0" if linked else "#E1E6EE"
            out.append(f'"doc_{d.id}" [shape=plain label=<<TABLE BORDER="1" COLOR="{border}" CELLBORDER="0" '
                       f'CELLSPACING="2" CELLPADDING="4" BGCOLOR="#FAFBFD" STYLE="ROUNDED">{"".join(rows)}</TABLE>>];')
            out.append(f'"co_{t}" -> "doc_{d.id}" [style=dashed arrowhead=none'
                       + ('' if linked else ' color="#E1E6EE"') + "];")
        for g in conflicts or []:
            for a, b in g["pairs"]:
                da, db = self.claims[a]["doc_id"], self.claims[b]["doc_id"]
                out.append(f'"doc_{da}":"{a}":e -> "doc_{db}":"{b}":e [dir=none color="#14213D" style=dashed '
                           f'penwidth=1.5 constraint=false];')
        out.append("}")
        return "\n".join(out)

    def _rel(self, src: str, t: str) -> str | None:
        return "sector" if t == "SECTOR" else self.store.relation_type(src, t)


def _path_text(src: str, dst: str, rel: str, topics: str) -> str:
    """영향 경로를 읽기 쉬운 문장으로: '마이크론 공시 → 고객사 엔비디아 (메모리·부품 가격 → 매출총이익률)'."""
    if rel == "same":
        return f"{src} 공시 → 같은 회사 {dst} 문장 ({topics})"
    if rel == "sector":
        return f"{src} 공시 → 섹터 전망 문장 ({topics})"
    role = {"supplier_to_customer": "고객사", "customer_to_supplier": "공급사", "competitor": "경쟁사"}.get(rel, "")
    return f"{src} 공시 → {role} {dst} ({topics})"


def _esc(s: str) -> str:
    return str(s).replace('"', "'")


def _h(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
