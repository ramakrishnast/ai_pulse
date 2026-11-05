"""Thin LLM client over OpenAI or Anthropic with a per-run cost guard and token logging."""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Callable

from .config import Settings, get_settings
from .db import LLMUsage, session_scope

log = logging.getLogger(__name__)

# (model, system, user, max_output_tokens, json_mode) -> (text, input_tokens, output_tokens)
Transport = Callable[[str, str, str, int, bool], tuple[str, int, int]]


class BudgetExceeded(RuntimeError):
    pass


class LLMUnavailable(RuntimeError):
    pass


def _openai_transport(settings: Settings) -> Transport:
    from openai import OpenAI

    kwargs = {"api_key": settings.openai_api_key, "timeout": settings.llm_timeout_seconds, "max_retries": 2}
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    client = OpenAI(**kwargs)

    def call(model: str, system: str, user: str, max_tokens: int, json_mode: bool):
        params = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_completion_tokens": max_tokens,
        }
        if json_mode:
            params["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**params)
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        return text, int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0)

    return call


def _anthropic_transport(settings: Settings) -> Transport:
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds, max_retries=2)

    def call(model: str, system: str, user: str, max_tokens: int, json_mode: bool):
        if json_mode:
            system += "\n\nRespond with a single valid JSON object and nothing else."
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content)
        return text, int(resp.usage.input_tokens or 0), int(resp.usage.output_tokens or 0)

    return call


def parse_json(text: str):
    """Parse a JSON object/array from model output, tolerating code fences and chatter."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON found in model output")
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start:end + 1])


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMClient:
    """Create one per pipeline run (budget is per instance)."""

    def __init__(self, settings: Settings | None = None, run_id: int | None = None,
                 transport: Transport | None = None, max_calls: int | None = None,
                 max_tokens: int | None = None, log_usage: bool = True, defer_logging: bool = False):
        self.settings = settings or get_settings()
        self.run_id = run_id
        self.usage = Usage()
        self.max_calls = self.settings.llm_max_calls_per_run if max_calls is None else max_calls
        self.max_tokens = self.settings.llm_max_tokens_per_run if max_tokens is None else max_tokens
        self.log_usage = log_usage
        # SQLite allows one writer: inside a pipeline stage we buffer usage rows and the
        # runner flushes them between stages (flush_usage).
        self.defer_logging = defer_logging
        self._pending: list[LLMUsage] = []
        self._lock = threading.Lock()
        self.provider = self.settings.llm_provider.lower()
        self._transport = transport
        if transport is None:
            if self.provider == "openai" and self.settings.openai_api_key:
                self._transport = _openai_transport(self.settings)
            elif self.provider == "anthropic" and self.settings.anthropic_api_key:
                self._transport = _anthropic_transport(self.settings)

    @property
    def available(self) -> bool:
        return self._transport is not None

    @property
    def budget_left(self) -> int:
        return max(0, self.max_calls - self.usage.calls)

    def _check_budget(self) -> None:
        if self.usage.calls >= self.max_calls:
            raise BudgetExceeded(f"LLM call cap reached ({self.max_calls} calls)")
        if self.usage.total_tokens >= self.max_tokens:
            raise BudgetExceeded(f"LLM token cap reached ({self.usage.total_tokens}/{self.max_tokens})")

    def _record(self, purpose: str, model: str, tin: int, tout: int, ok: bool) -> None:
        log.info("llm %-7s model=%s in=%d out=%d ok=%s | run total calls=%d tokens=%d",
                 purpose, model, tin, tout, ok, self.usage.calls, self.usage.total_tokens)
        if not self.log_usage:
            return
        with self._lock:
            self._pending.append(LLMUsage(run_id=self.run_id, purpose=purpose, provider=self.provider,
                                          model=model, input_tokens=tin, output_tokens=tout, ok=ok))
        if not self.defer_logging:
            self.flush_usage()

    def flush_usage(self) -> None:
        with self._lock:
            rows, self._pending = self._pending, []
        if not rows:
            return
        try:
            with session_scope() as s:
                s.add_all(rows)
        except Exception:  # never fail a run because usage logging failed
            log.exception("failed to log LLM usage")

    def complete(self, system: str, user: str, purpose: str, model: str | None = None,
                 max_output_tokens: int | None = None, json_mode: bool = False) -> str:
        if not self.available:
            raise LLMUnavailable("no LLM configured (set LLM_PROVIDER and its API key)")
        model = model or self.settings.llm_model
        max_out = max_output_tokens or self.settings.llm_max_output_tokens
        with self._lock:
            self._check_budget()
            self.usage.calls += 1
        try:
            text, tin, tout = self._transport(model, system, user, max_out, json_mode)
        except Exception:
            self._record(purpose, model, 0, 0, ok=False)
            raise
        with self._lock:
            self.usage.input_tokens += tin
            self.usage.output_tokens += tout
        self._record(purpose, model, tin, tout, ok=True)
        return text

    def complete_json(self, system: str, user: str, purpose: str, **kwargs):
        return parse_json(self.complete(system, user, purpose, json_mode=True, **kwargs))
