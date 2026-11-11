"""주장 유형(6종)과 세부 토픽 사전.

- 국문(KO) 패턴: 내부 문서 문장에서 토픽·방향을 찾을 때 사용
- 영문(EN) 패턴: 공시 문장에서 근거와 방향(stance)을 찾을 때 사용
- 전파 규칙: 한 회사의 공시가 관계(공급사·고객·경쟁사)를 따라 어떤 토픽의 판단을 흔드는지 정의
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CLAIM_TYPES = ["수요", "공급/생산", "매출 성장", "수익성", "가이던스", "경쟁 구도"]


@dataclass(frozen=True)
class Topic:
    key: str
    label: str
    claim_type: str
    scope: str  # market: 다른 회사 공시도 근거 가능 / company: 해당 회사 공시만 / related: 해당 회사+경쟁사
    ko_any: tuple            # 이 중 하나가 있어야 토픽 후보
    ko_also: tuple = ()      # (선택) 이 중 하나도 있어야 함
    ko_up: tuple = ()
    ko_down: tuple = ()
    en_query: tuple = ()     # BM25 검색어 (국문 주장 → 영문 검색어)
    en_expand: tuple = ()    # 재탐색 때 추가하는 동의어
    en_anchor: tuple = ()    # 근거 문장에 있어야 하는 표현 (정규식)
    en_terms: tuple = ()     # (선택) 근거 문장에 함께 있어야 하는 표현
    en_up: tuple = ()        # 근거 문장의 + 방향 표현
    en_down: tuple = ()      # 근거 문장의 − 방향 표현
    interpretive: bool = False   # 방향을 기계적으로 정하기 어려운 토픽 (사람 판단)
    xbrl_metric: str | None = None
    dir_labels: tuple = ("증가", "감소")


TOPICS: dict[str, Topic] = {
    "guidance": Topic(
        key="guidance", label="실적 가이던스", claim_type="가이던스", scope="company",
        ko_any=("다음 분기", "가이던스", "전망치"), ko_also=("매출", "실적", "이익"),
        ko_up=("늘어", "증가", "상향", "웃돌"), ko_down=("줄어", "감소", "하향", "밑돌"),
        en_query=("outlook", "guidance", "revenue", "quarter"),
        xbrl_metric="guidance_vs_last", dir_labels=("증가", "감소"),
    ),
    "memory_balance": Topic(
        key="memory_balance", label="메모리·부품 수급", claim_type="공급/생산", scope="market",
        ko_any=("공급 부족", "공급 과잉", "공급을 웃", "공급 제약", "수급", "출하를 제한", "공급 초과"),
        ko_up=("공급 부족", "공급을 웃", "공급 제약", "출하를 제한", "타이트"),
        ko_down=("공급 과잉", "공급 초과", "완화"),
        en_query=("demand", "supply", "constrained", "outpacing", "memory"),
        en_expand=("shortage", "allocation", "capacity", "supply constraints"),
        en_anchor=(r"\bsupply\b(?!\s+chain)",),
        en_up=(r"outpacing", r"constrain", r"shortage", r"supply allocation", r"greater than our ability"),
        en_down=(r"oversupply", r"excess supply", r"supply exceeds", r"\bglut", r"\beased\b", r"easing"),
        dir_labels=("공급 부족(타이트)", "공급 과잉(완화)"),
    ),
    "gross_margin": Topic(
        key="gross_margin", label="매출총이익률", claim_type="수익성", scope="company",
        ko_any=("매출총이익률", "이익률", "마진"),
        ko_up=("개선", "상승", "확대", "웃돌", "높아"), ko_down=("압박", "하락", "밑돌", "머물", "악화", "축소", "낮아"),
        en_query=("gross", "margin", "improved", "increased", "mix", "selling", "prices"),
        en_expand=("profitability", "cost", "percentage"),
        en_anchor=(r"gross margin",),
        en_up=(r"improv", r"\bincrease", r"\bhigher\b", r"expand"),
        en_down=(r"declin", r"decrease", r"\blower\b", r"compress", r"pressure"),
        xbrl_metric="gross_margin", dir_labels=("상승", "하락"),
    ),
    "datacenter_growth": Topic(
        key="datacenter_growth", label="데이터센터 매출 성장", claim_type="매출 성장", scope="company",
        ko_any=("데이터센터",), ko_also=("매출", "성장", "동력"),
        ko_up=("핵심 동력", "가속", "확대", "증가", "성장하"), ko_down=("둔화", "감소", "축소", "역성장"),
        en_query=("data", "center", "revenue", "increase", "growth"),
        en_expand=("segment", "demand", "ramp"),
        en_anchor=(r"data center",),
        en_terms=(r"revenue",),
        en_up=(r"\bincrease", r"\bup \d", r"\bgrew\b", r"growth", r"more than doubled"),
        en_down=(r"decrease", r"declin", r"\bdown \d"),
        dir_labels=("성장", "둔화"),
    ),
    "ai_demand": Topic(
        key="ai_demand", label="AI·데이터센터 수요", claim_type="수요", scope="market",
        ko_any=("수요",), ko_also=("AI", "서버", "데이터센터"),
        ko_up=("가속", "증가", "확대", "견조", "웃돌"), ko_down=("둔화", "감소", "위축", "약화", "꺾"),
        en_query=("AI", "demand", "data", "center", "accelerated", "growth"),
        en_expand=("hyperscale", "cloud", "strong", "robust"),
        en_anchor=(r"\bAI\b", r"data center", r"hyperscale", r"\bcloud\b"),
        en_terms=(r"\bdemand",),
        en_up=(r"accelerat", r"outpacing", r"strong demand", r"demand growth", r"\brobust\b",
               r"strengthened", r"increased demand", r"end-demand"),
        en_down=(r"\bslow(ed|ing|er)?\b", r"soften", r"\bweak", r"declin", r"diminish", r"lower demand", r"decreas"),
        dir_labels=("가속", "둔화"),
    ),
    "capacity": Topic(
        key="capacity", label="설비·생산능력", claim_type="공급/생산", scope="company",
        ko_any=("팹", "설비투자", "증설", "캐파", "생산능력"),
        ko_up=("증가", "확대", "늘어"), ko_down=("감소", "축소", "지연", "줄어"),
        en_query=("fab", "wafer", "capacity", "capital", "expenditures", "output"),
        en_expand=("cleanroom", "construction", "manufacturing"),
        en_anchor=(r"\bfab\b", r"wafer capacity", r"wafer output", r"capital expenditures", r"cleanroom",
                   r"production capacity"),
        en_up=(r"\bexpand", r"\bincrease", r"\bhigher\b", r"add new", r"\boutput\b", r"broke ground"),
        en_down=(r"\breduc", r"\bcut\b", r"\bdelay", r"\bpaus"),
        dir_labels=("확대", "축소"),
    ),
    "competition": Topic(
        key="competition", label="경쟁 구도", claim_type="경쟁 구도", scope="related",
        ko_any=("경쟁", "점유율", "생태계"),
        ko_up=("확대", "우위", "강화"), ko_down=("제한적", "축소", "약화", "잠식"),
        en_query=("competitive", "competitors", "ecosystem", "platform", "share", "Instinct", "GPU"),
        en_expand=("demand", "launch", "accelerators"),
        en_anchor=(r"compet", r"market share", r"\becosystem", r"\bInstinct", r"\bCUDA\b"),
        en_terms=(r"compet", r"market share", r"ecosystem", r"\bdemand", r"adoption", r"\blaunch", r"customers?\b"),
        interpretive=True, dir_labels=("강화", "약화"),
    ),
    "memory_price": Topic(
        key="memory_price", label="메모리·부품 가격", claim_type="수익성", scope="market",
        ko_any=("판가", "가격", "원가"),
        ko_up=("상승", "인상", "오르"), ko_down=("하락", "인하", "내리"),
        en_query=("average", "selling", "prices", "pricing", "memory"),
        en_anchor=(r"pric",),
        en_up=(r"increased pricing", r"increase in average selling prices", r"increases in average selling prices",
               r"elevated memory"),
        en_down=(r"declines? in average selling prices", r"pricing pressure", r"lower prices"),
        dir_labels=("상승", "하락"),
    ),
    "revenue_growth": Topic(
        key="revenue_growth", label="매출 성장", claim_type="매출 성장", scope="company",
        ko_any=("매출",),
        ko_up=("반등", "증가", "성장", "늘어", "확대"), ko_down=("감소", "역성장", "둔화", "줄어"),
        en_query=("net", "revenue", "increased", "decreased", "compared", "prior", "year"),
        en_expand=("segment", "sales"),
        en_anchor=(r"revenue",),
        en_up=(r"\bincrease", r"\bgrew\b", r"\bup \d"),
        en_down=(r"decrease", r"declin", r"\bdown \d", r"\blower\b"),
        xbrl_metric="revenue_growth", dir_labels=("증가", "감소"),
    ),
}

# 국문 문장의 주 토픽을 고르는 우선순위
PRIORITY = ["guidance", "memory_balance", "gross_margin", "datacenter_growth", "ai_demand",
            "capacity", "competition", "memory_price", "revenue_growth"]

# 세그먼트 한-영 대응 (매출 성장 주장에 세그먼트가 있으면 근거 문장에도 같은 세그먼트가 있어야 함)
SEGMENTS = {"게이밍": "Gaming", "데이터센터": "Data Center", "클라이언트": "Client", "임베디드": "Embedded"}

FORECAST_MARKERS = ("것이다", "것으로 보", "전망", "예상", "가능성", "될 것", "할 것")

# 관계별 토픽 전파 규칙: (관계, 공시가 건드린 토픽) -> 상대 회사 판단 중 흔들리는 토픽
PROPAGATION = {
    "same": None,  # 같은 회사: 공시가 건드린 토픽 전부 (+ ai_demand → datacenter_growth)
    "supplier_to_customer": {
        "ai_demand": ["ai_demand", "datacenter_growth"],
        "memory_balance": ["memory_balance"],
        "memory_price": ["memory_price", "gross_margin"],
    },
    "customer_to_supplier": {
        "ai_demand": ["ai_demand"],
        "datacenter_growth": ["ai_demand"],
    },
    "competitor": {
        "datacenter_growth": ["competition"],
        "competition": ["competition"],
        "ai_demand": ["ai_demand"],
    },
}
SAME_COMPANY_EXTRA = {"ai_demand": ["datacenter_growth"]}


def rx_any(patterns, text: str, flags=re.IGNORECASE) -> bool:
    for p in patterns:
        if p in (r"\bAI\b",):
            if re.search(p, text):  # 대문자 AI만 (maintain, chain 오탐 방지)
                return True
        elif re.search(p, text, flags):
            return True
    return False


def first_pos(patterns, text: str) -> int:
    best = -1
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m and (best < 0 or m.start() < best):
            best = m.start()
    return best
