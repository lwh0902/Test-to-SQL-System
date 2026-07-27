"""DeepSeek (Anthropic-compatible) model adapter for Agent Harness.

复用项目既有环境变量：
- LLM_API_KEY
- LLM_BASE_URL (default https://api.deepseek.com/anthropic)
- LLM_MODEL   (default deepseek-v4-flash)
- LLM_MODEL_PRO (optional, default deepseek-v4-pro)

按角色分流：
- insight / review / light tasks → flash + low thinking
- report / heavy analysis       → pro  + medium thinking
- export / pure transform       → 不调模型

thinking 通过 system 指令约束「思考强度」，不把 chain-of-thought 写回 A2A 事件或工件。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    FLASH = "flash"
    PRO = "pro"


class ThinkingLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class RoleModelPolicy:
    tier: ModelTier
    thinking: ThinkingLevel
    max_tokens: int = 1024


def role_model_policy(agent_name: str) -> RoleModelPolicy:
    """按项目角色选择模型档位与思考强度。"""
    name = (agent_name or "").lower()
    # Front-door Supervisor decision is flash-only (dev_spec P0). Diagnosis playbook
    # narrative work still goes through Report (pro), not this policy key.
    if name in ("supervisor", "supervisor_decision", "router"):
        return RoleModelPolicy(ModelTier.FLASH, ThinkingLevel.NONE, max_tokens=400)
    if name in ("report",):
        return RoleModelPolicy(ModelTier.PRO, ThinkingLevel.MEDIUM, max_tokens=2048)
    if name in ("insight",):
        return RoleModelPolicy(ModelTier.FLASH, ThinkingLevel.LOW, max_tokens=1024)
    if name in ("review",):
        # Review Agent: flash is enough, but allow a bit more tokens for reasons
        return RoleModelPolicy(ModelTier.FLASH, ThinkingLevel.LOW, max_tokens=1024)
    if name in ("export", "query"):
        # query 走 LangGraph；export 走确定性渲染
        return RoleModelPolicy(ModelTier.FLASH, ThinkingLevel.NONE, max_tokens=256)
    return RoleModelPolicy(ModelTier.FLASH, ThinkingLevel.LOW, max_tokens=1024)


def resolve_model_name(tier: ModelTier) -> str:
    flash = os.getenv("LLM_MODEL", "deepseek-v4-flash")
    if tier == ModelTier.PRO:
        return os.getenv("LLM_MODEL_PRO") or os.getenv("LLM_MODEL_REASONER") or "deepseek-v4-pro"
    return flash


def resolve_thinking(level: ThinkingLevel) -> dict | None:
    if level == ThinkingLevel.NONE:
        return None
    return {"level": level.value}


_THINKING_INSTRUCTION = {
    ThinkingLevel.NONE: "直接给出最终结构化结果，不要输出思考过程。",
    ThinkingLevel.LOW: (
        "可做简短核对后直接给出结果。"
        "不要输出逐步思维链；只输出最终 JSON。"
    ),
    ThinkingLevel.MEDIUM: (
        "在内部完成必要的数据对照与因果梳理，但回复中只给最终结构化 JSON，"
        "不要输出 chain-of-thought 或私有推理过程。"
    ),
    ThinkingLevel.HIGH: (
        "充分分析证据与反例后再作答；回复仍只能是最终 JSON，禁止输出思维链原文。"
    ),
}


@dataclass
class ModelRequest:
    system: str
    user: str
    tier: ModelTier = ModelTier.FLASH
    thinking: ThinkingLevel = ThinkingLevel.LOW
    max_tokens: int = 1024
    expect_json: bool = True
    temperature: float | None = None


@dataclass
class ModelResponse:
    ok: bool
    text: str = ""
    json_payload: dict | list | None = None
    model: str = ""
    error: str | None = None
    usage: dict | None = None


def _extract_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        # TextBlock
        if hasattr(block, "text") and getattr(block, "text", None):
            # skip thinking-only blocks if API returns separate types
            bname = type(block).__name__
            if bname in ("ThinkingBlock", "Thinking"):
                continue
            parts.append(block.text)
    return "\n".join(parts).strip()


def _parse_json_loose(text: str) -> dict | list | None:
    if not text:
        return None
    cleaned = text.strip()
    # strip markdown fence
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    # direct
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # first {...} or [...]
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = cleaned.find(open_c)
        end = cleaned.rfind(close_c)
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except Exception:
                continue
    return None


class ModelAdapter:
    """统一 LLM 调用入口，供 Harness / 各 Agent 注入使用。"""

    def __init__(self, sync_client: Any = None, async_client: Any = None):
        self._sync = sync_client
        self._async = async_client

    def _ensure_sync(self):
        if self._sync is None:
            from anthropic import Anthropic

            self._sync = Anthropic(
                api_key=os.getenv("LLM_API_KEY"),
                base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
            )
        return self._sync

    def _ensure_async(self):
        if self._async is None:
            from anthropic import AsyncAnthropic

            self._async = AsyncAnthropic(
                api_key=os.getenv("LLM_API_KEY"),
                base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
            )
        return self._async

    def _build_kwargs(self, req: ModelRequest) -> dict:
        model = resolve_model_name(req.tier)
        thinking_note = _THINKING_INSTRUCTION.get(req.thinking, _THINKING_INSTRUCTION[ThinkingLevel.LOW])
        system = (
            f"{req.system.strip()}\n\n"
            f"【输出纪律】{thinking_note}\n"
            "禁止在输出中包含密码、密钥、原始凭证或隐藏推理。"
        )
        if req.expect_json:
            system += "\n只输出合法 JSON，不要 Markdown 说明文字（代码围栏可有可无）。"

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": req.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": req.user}],
        }
        # Optional temperature if caller wants more deterministic JSON
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature

        # DeepSeek thinking — pass only if API supports; ignore failures at call site
        thinking = resolve_thinking(req.thinking)
        if thinking is not None:
            # Prefer extra_body for anthropic-compatible gateways that accept thinking
            kwargs["extra_body"] = {"thinking": thinking}
        return kwargs

    def complete(self, req: ModelRequest) -> ModelResponse:
        model = resolve_model_name(req.tier)
        try:
            client = self._ensure_sync()
            kwargs = self._build_kwargs(req)
            # If extra_body unsupported, retry without it
            try:
                response = client.messages.create(**kwargs)
            except TypeError:
                kwargs.pop("extra_body", None)
                response = client.messages.create(**kwargs)
            except Exception as e:
                # some gateways reject unknown thinking — retry plain
                if "thinking" in str(e).lower() or "extra_body" in str(e).lower():
                    kwargs.pop("extra_body", None)
                    response = client.messages.create(**kwargs)
                else:
                    raise

            text = _extract_text(response)
            payload = _parse_json_loose(text) if req.expect_json else None
            usage = None
            if getattr(response, "usage", None):
                usage = {
                    "input_tokens": getattr(response.usage, "input_tokens", None),
                    "output_tokens": getattr(response.usage, "output_tokens", None),
                }
            return ModelResponse(
                ok=True,
                text=text,
                json_payload=payload if isinstance(payload, (dict, list)) else None,
                model=model,
                usage=usage,
            )
        except Exception as e:
            logger.exception("ModelAdapter.complete failed model=%s", model)
            return ModelResponse(ok=False, error=str(e), model=model)

    async def acomplete(self, req: ModelRequest) -> ModelResponse:
        model = resolve_model_name(req.tier)
        try:
            client = self._ensure_async()
            kwargs = self._build_kwargs(req)
            try:
                response = await client.messages.create(**kwargs)
            except TypeError:
                kwargs.pop("extra_body", None)
                response = await client.messages.create(**kwargs)
            except Exception as e:
                if "thinking" in str(e).lower() or "extra_body" in str(e).lower():
                    kwargs.pop("extra_body", None)
                    response = await client.messages.create(**kwargs)
                else:
                    raise

            text = _extract_text(response)
            payload = _parse_json_loose(text) if req.expect_json else None
            usage = None
            if getattr(response, "usage", None):
                usage = {
                    "input_tokens": getattr(response.usage, "input_tokens", None),
                    "output_tokens": getattr(response.usage, "output_tokens", None),
                }
            return ModelResponse(
                ok=True,
                text=text,
                json_payload=payload if isinstance(payload, (dict, list)) else None,
                model=model,
                usage=usage,
            )
        except Exception as e:
            logger.exception("ModelAdapter.acomplete failed model=%s", model)
            return ModelResponse(ok=False, error=str(e), model=model)


# process-wide singleton for agents
_default_adapter: ModelAdapter | None = None


def get_model_adapter() -> ModelAdapter:
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = ModelAdapter()
    return _default_adapter
