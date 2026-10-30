"""Orchestrator LLM providers.

The model runs on a separate inference server; the backend is the only
component that talks to it (browsers never call it directly). Provider
adapters hide the wire format. `openai` covers any OpenAI-compatible
`/chat/completions` API (vLLM, SGLang, llama.cpp server, TGI's OpenAI route,
LM Studio, ...). Another API format is added by implementing `LLMProvider` and
registering it in `PROVIDERS`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx

from ..config import get_settings
from ..security.redact import redact


class LLMError(RuntimeError):
    """kind: not_configured | dns | connect | timeout | tls | auth | http | protocol | unsupported"""

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.status = status
        self.message = message


@dataclass
class ToolCall:
    name: str
    arguments: str  # raw JSON text as produced by the model
    id: str = ""


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)
    latency_seconds: float = 0.0


class LLMProvider:
    name = "base"

    def list_models(self) -> list[dict]:
        raise NotImplementedError

    def chat(self, messages: list[dict], *, tools: list[dict] | None = None, tool_choice=None,
             response_format: dict | None = None, max_tokens: int | None = None, temperature: float = 0.2,
             timeout: float | None = None, extra_body: dict | None = None) -> ChatResult:
        raise NotImplementedError

    def chat_stream(self, messages: list[dict], *, max_tokens: int | None = None,
                    timeout: float | None = None) -> Iterator[str]:
        raise NotImplementedError


def _classify(e: Exception) -> LLMError:
    msg = redact(str(e))[:300]
    if isinstance(e, httpx.TimeoutException):
        return LLMError("timeout", msg or "request timed out")
    if isinstance(e, httpx.ConnectError):
        low = msg.lower()
        if "name or service not known" in low or "nodename nor servname" in low or "temporary failure in name resolution" in low or "getaddrinfo" in low:
            return LLMError("dns", msg)
        if "ssl" in low or "certificate" in low:
            return LLMError("tls", msg)
        return LLMError("connect", msg)
    if isinstance(e, httpx.HTTPError):
        return LLMError("connect", msg)
    return LLMError("protocol", msg)


class OpenAICompatibleProvider(LLMProvider):
    name = "openai"

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 120.0,
                 connect_timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.connect_timeout = connect_timeout

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _timeout(self, total: float | None) -> httpx.Timeout:
        t = total if total is not None else self.timeout
        return httpx.Timeout(t, connect=min(self.connect_timeout, t))

    def _check(self, r: httpx.Response) -> None:
        if r.status_code in (401, 403):
            raise LLMError("auth", f"HTTP {r.status_code}: {redact(r.text[:200])}", r.status_code)
        if r.status_code >= 400:
            raise LLMError("http", f"HTTP {r.status_code}: {redact(r.text[:300])}", r.status_code)

    def list_models(self) -> list[dict]:
        try:
            r = httpx.get(f"{self.base_url}/models", headers=self._headers(), timeout=self._timeout(30))
        except Exception as e:
            raise _classify(e) from e
        self._check(r)
        try:
            data = r.json()
        except ValueError as e:
            raise LLMError("protocol", "models endpoint did not return JSON") from e
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise LLMError("protocol", "unexpected /models response shape")
        return [m for m in items if isinstance(m, dict)]

    def chat(self, messages, *, tools=None, tool_choice=None, response_format=None, max_tokens=None,
             temperature=0.2, timeout=None, extra_body: dict | None = None) -> ChatResult:
        body: dict = {"model": self.model, "messages": messages, "temperature": temperature,
                      "max_tokens": max_tokens or get_settings().llm_max_output_tokens, "stream": False}
        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        if response_format:
            body["response_format"] = response_format
        if extra_body:
            body.update(extra_body)
        t0 = time.monotonic()
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=body,
                           timeout=self._timeout(timeout))
        except Exception as e:
            raise _classify(e) from e
        self._check(r)
        try:
            data = r.json()
            choice = data["choices"][0]
            msg = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError("protocol", f"unexpected chat response: {redact(r.text[:200])}") from e
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = (tc or {}).get("function") or {}
            args = fn.get("arguments")
            if not isinstance(args, str):
                args = json.dumps(args)
            calls.append(ToolCall(name=fn.get("name", ""), arguments=args or "", id=tc.get("id", "")))
        content = msg.get("content") or ""
        if isinstance(content, list):  # some servers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return ChatResult(content=content, tool_calls=calls, finish_reason=choice.get("finish_reason") or "",
                          model=data.get("model", ""), usage=data.get("usage") or {},
                          latency_seconds=time.monotonic() - t0)

    def chat_stream(self, messages, *, max_tokens=None, timeout=None) -> Iterator[str]:
        body = {"model": self.model, "messages": messages, "stream": True,
                "max_tokens": max_tokens or get_settings().llm_max_output_tokens}
        try:
            with httpx.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body,
                              timeout=self._timeout(timeout)) as r:
                if r.status_code >= 400:
                    r.read()
                    self._check(r)
                for line in r.iter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        chunk = json.loads(payload)
                    except ValueError as e:
                        raise LLMError("protocol", "malformed stream chunk") from e
                    for ch in chunk.get("choices") or []:
                        delta = (ch.get("delta") or {}).get("content")
                        if delta:
                            yield delta
        except LLMError:
            raise
        except Exception as e:
            raise _classify(e) from e


PROVIDERS: dict[str, type[LLMProvider]] = {"openai": OpenAICompatibleProvider}


def get_provider() -> LLMProvider:
    s = get_settings()
    if not s.llm_configured:
        raise LLMError("not_configured", "LLM_BASE_URL and LLM_MODEL must be set")
    cls = PROVIDERS.get(s.llm_provider)
    if cls is None:
        raise LLMError("unsupported", f"LLM_PROVIDER={s.llm_provider!r} has no adapter (available: {', '.join(PROVIDERS)})")
    return cls(s.llm_base_url, s.llm_model, s.llm_api_key, s.llm_timeout_seconds, s.llm_connect_timeout_seconds)
