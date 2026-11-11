"""LLM 어댑터 (선택). 키가 없으면 RedPen은 규칙 기반 모드로 동작한다.

환경 변수
  REDPEN_LLM_PROVIDER   anthropic | openai | openai_compatible (사내 모델)
  REDPEN_LLM_MODEL      판정·검증용 고성능 모델 (기본: claude-sonnet-5-5 / gpt-4.1)
  REDPEN_LLM_MODEL_FAST 분해·분류용 경량 모델 (기본: claude-haiku-5-5 / gpt-4.1-mini)
  REDPEN_LLM_BASE_URL   openai_compatible일 때 사내 엔드포인트
  ANTHROPIC_API_KEY / OPENAI_API_KEY / REDPEN_LLM_API_KEY
  REDPEN_LLM_MAX_CALLS  프로세스당 누적 호출 상한 (기본 1000, 0 = 무제한). 넘으면 규칙 기반으로 대체
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

DEFAULTS = {
    "anthropic": ("claude-sonnet-5-5", "claude-haiku-5-5"),
    "openai": ("gpt-4.1", "gpt-4.1-mini"),
    "openai_compatible": ("internal-llm", "internal-llm"),
}


class LLMClient:
    def __init__(self, provider: str, api_key: str, model: str, model_fast: str, base_url: str | None = None,
                 transport=None):
        self.provider, self.api_key = provider, api_key
        self.model, self.model_fast, self.base_url = model, model_fast, base_url
        self._transport = transport or _http_post
        self.calls = 0
        self.max_calls = int(os.getenv("REDPEN_LLM_MAX_CALLS", "1000") or 0)

    @classmethod
    def from_env(cls) -> "LLMClient | None":
        provider = os.getenv("REDPEN_LLM_PROVIDER", "").strip()
        if not provider:
            if os.getenv("ANTHROPIC_API_KEY") and os.getenv("REDPEN_USE_ANTHROPIC_KEY") == "1":
                provider = "anthropic"
            else:
                return None
        key = os.getenv("REDPEN_LLM_API_KEY") or (os.getenv("ANTHROPIC_API_KEY") if provider == "anthropic"
                                                  else os.getenv("OPENAI_API_KEY"))
        if not key:
            return None
        m, mf = DEFAULTS.get(provider, ("", ""))
        return cls(provider, key, os.getenv("REDPEN_LLM_MODEL", m), os.getenv("REDPEN_LLM_MODEL_FAST", mf),
                   os.getenv("REDPEN_LLM_BASE_URL"))

    @property
    def label(self) -> str:
        return f"{self.provider} · {self.model} / {self.model_fast}"

    def complete_json(self, prompt: str, role: str = "judge") -> dict:
        model = self.model_fast if role == "extract" else self.model
        if self.max_calls and self.calls >= self.max_calls:
            raise RuntimeError("LLM 호출 상한 도달 (REDPEN_LLM_MAX_CALLS)")  # 호출부에서 규칙 기반으로 대체
        self.calls += 1
        if self.provider == "anthropic":
            body = {"model": model, "max_tokens": 600, "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}]}
            headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            res = self._transport("https://api.anthropic.com/v1/messages", body, headers)
            text = "".join(b.get("text", "") for b in res.get("content", []))
        else:
            base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
            body = {"model": model, "temperature": 0, "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"}}
            headers = {"authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
            res = self._transport(f"{base}/chat/completions", body, headers)
            text = res["choices"][0]["message"]["content"]
        return parse_json(text)


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("JSON 응답 없음")
    return json.loads(m.group(0))


def _http_post(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - 지정한 API 엔드포인트만 호출
        return json.loads(r.read().decode("utf-8"))
