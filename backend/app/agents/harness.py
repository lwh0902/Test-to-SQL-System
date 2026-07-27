"""所有角色共用的受控 Harness（按 dev_spec 实现）。

每个 Agent 必须具备：
- context builder → AgentContextPackage（私有 working/experience + 允许输入）
- model adapter（timeout + token budget）
- role Skill
- tool gateway（白名单）
- memory adapter（私有 working + experience，强制 scrub）
- artifact adapter
- output validator
- timeout / retry / token budget
- safe event emitter（永不泄漏 prompt / CoT / 凭据 / 原始行）
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.a2a.contracts import A2AMessage
from app.agents.context_policy import (
    AgentContextPackage,
    append_experience_note,
    build_agent_context_package,
    merge_working,
    scrub_memory,
)
from app.services.agent_runtime_store import (
    get_experience_memory,
    get_working_memory,
    save_artifact,
    set_experience_memory,
    set_working_memory,
)

logger = logging.getLogger(__name__)

# 禁止出现在 safe event 中的字段
_PRIVATE_EVENT_KEYS = frozenset({
    "prompt", "system_prompt", "chain_of_thought", "cot", "reasoning",
    "credentials", "password", "api_key", "token", "secret",
    "raw_memory", "raw_rows", "private_memory", "hidden",
})

# 非 Query 工件 payload 中禁止的键
_FORBIDDEN_PAYLOAD_KEYS = frozenset({
    "rows", "raw_rows", "credentials", "password", "api_key",
    "chain_of_thought", "prompt", "system_prompt",
})


@dataclass(frozen=True)
class AgentSkill:
    """角色能力包：输入/输出 schema、允许工具、程序名。Skill 不能替代 Harness。"""
    name: str
    allowed_tools: frozenset[str]
    input_schema: str
    output_schema: str
    procedure: str = ""
    policies: tuple[str, ...] = ()


@dataclass
class HarnessConfig:
    """超时 / 重试 / token 预算。"""
    timeout_seconds: float = 120.0
    max_retries: int = 1
    token_budget: int = 8000
    retry_backoff_seconds: float = 0.5


class ToolGateway:
    """工具白名单网关。只有 Skill.allowed_tools 中的工具可调用。"""

    def __init__(self, allowed: frozenset[str]):
        self._allowed = allowed

    def is_allowed(self, tool_name: str) -> bool:
        return tool_name in self._allowed

    def require(self, tool_name: str) -> None:
        if not self.is_allowed(tool_name):
            raise PermissionError(f"工具未授权: {tool_name}（allowed={sorted(self._allowed)}）")


class MemoryAdapter:
    """私有记忆适配器。

    working: task_id + agent_name + session_id + user_id + space_id
    experience: agent_name + space_id
    所有写入强制 deep scrub（禁止 raw rows / 凭据 / CoT）。
    """

    def __init__(self, agent_name: str, message: A2AMessage):
        self.agent_name = agent_name
        self.message = message

    def _working_key(self) -> dict:
        return {
            "task_id": self.message.task_id,
            "agent_name": self.agent_name,
            "session_id": self.message.session_id,
            "user_id": self.message.user_id,
            "space_id": self.message.space_id,
        }

    def get_working(self) -> dict | None:
        raw = get_working_memory(**self._working_key())
        return scrub_memory(raw) if raw else None

    def set_working(self, memory: dict) -> None:
        set_working_memory(**self._working_key(), memory=scrub_memory(memory))

    def update_working(self, updates: dict) -> dict:
        merged = merge_working(self.get_working(), updates)
        self.set_working(merged)
        return merged

    def get_experience(self) -> dict | None:
        raw = get_experience_memory(self.agent_name, self.message.space_id)
        return scrub_memory(raw) if raw else None

    def set_experience(self, memory: dict) -> None:
        set_experience_memory(self.agent_name, self.message.space_id, scrub_memory(memory))

    def append_experience_note(self, note: str, *, meta: Optional[dict] = None) -> dict:
        exp = append_experience_note(self.get_experience(), note, meta=meta)
        self.set_experience(exp)
        return exp


class ArtifactAdapter:
    """工件适配器：始终带完整 task/session/user/space 作用域。"""

    def __init__(self, agent_name: str, message: A2AMessage):
        self.agent_name = agent_name
        self.message = message

    def save(self, artifact_type: str, status: str, payload: dict) -> str:
        return save_artifact(
            task_id=self.message.task_id,
            session_id=self.message.session_id,
            user_id=self.message.user_id,
            space_id=self.message.space_id,
            artifact_type=artifact_type,
            source_agent=self.agent_name,
            status=status,
            payload=payload,
        )


class OutputValidator:
    """输出校验：必须有 artifact_id；非 Query 工件禁止 raw rows / 敏感字段。"""

    def __init__(self, output_schema: str):
        self.output_schema = output_schema

    def validate(self, result: dict) -> dict:
        if not isinstance(result, dict):
            raise ValueError("Harness 输出必须是 dict")
        if not result.get("artifact_id"):
            raise ValueError("Harness 输出缺少 artifact_id")
        payload = result.get("payload")
        if payload is None:
            raise ValueError("Harness 输出缺少 payload")
        if self.output_schema not in ("QueryResult", "QueryRequest"):
            self._reject_sensitive(payload)
        return result

    def _reject_sensitive(self, obj: Any, path: str = "payload") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = str(k).lower()
                if lk in _FORBIDDEN_PAYLOAD_KEYS or lk == "rows":
                    raise ValueError(f"非 Query 输出禁止包含 raw rows/敏感字段: {path}.{k}")
                self._reject_sensitive(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                self._reject_sensitive(item, f"{path}[{i}]")


class SafeEventEmitter:
    """安全事件发射器：过滤私有字段后再回调。"""

    def __init__(self, callback: Optional[Callable[[str, dict], None]] = None):
        self._callback = callback
        self.history: list[tuple[str, dict]] = []

    @staticmethod
    def _sanitize(data: dict) -> dict:
        clean = {}
        for k, v in data.items():
            if str(k).lower() in _PRIVATE_EVENT_KEYS:
                continue
            if isinstance(v, dict):
                clean[k] = SafeEventEmitter._sanitize(v)
            else:
                clean[k] = v
        return clean

    def emit(self, event: str, data: dict) -> None:
        safe = self._sanitize(data if isinstance(data, dict) else {})
        self.history.append((event, safe))
        if self._callback:
            try:
                self._callback(event, safe)
            except Exception:
                logger.exception("safe event callback failed: %s", event)


@dataclass
class HarnessContext:
    """单次执行上下文：由 context builder 组装。"""
    message: A2AMessage
    skill: AgentSkill
    config: HarnessConfig
    tools: ToolGateway
    memory: MemoryAdapter
    artifacts: ArtifactAdapter
    events: SafeEventEmitter
    package: AgentContextPackage
    model: Any = None
    token_used: int = 0
    _model_loader: Any = None

    @property
    def working_memory(self) -> dict:
        return dict(self.package.working or {})

    @property
    def experience_memory(self) -> dict:
        return dict(self.package.experience or {})

    @property
    def allowed_inputs(self) -> dict:
        return dict(self.package.inputs or {})

    def get_model(self) -> Any:
        """Lazy model adapter — not loaded until a role actually needs LLM."""
        if self.model is not None:
            return self.model
        if self._model_loader is not None:
            self.model = self._model_loader()
            return self.model
        return None

    def model_bundle(self) -> dict:
        return self.package.as_model_bundle()

    def refresh_package(self) -> AgentContextPackage:
        """Reload package after memory writes during execute."""
        self.package = build_agent_context_package(
            agent_name=self.package.agent_name,
            message=self.message,
            skill_name=self.skill.name,
            working=self.memory.get_working(),
            experience=self.memory.get_experience(),
            allowed_tools=self.skill.allowed_tools,
            policies=self.skill.policies,
        )
        return self.package


class AgentHarness:
    """通用 Harness 基类。

    子类实现 `execute(ctx) -> dict`，返回 {"artifact_id", "payload", ...}。
    `handle` 负责：目标校验 → 构建上下文包 → 超时/重试 → 校验输出 → 安全事件。
    """

    def __init__(
        self,
        name: str,
        skill: AgentSkill,
        config: Optional[HarnessConfig] = None,
        event_callback: Optional[Callable[[str, dict], None]] = None,
    ):
        self.name = name
        self.skill = skill
        self.config = config or HarnessConfig()
        self._event_callback = event_callback

    # ----- context builder -----
    def build_context(self, message: A2AMessage) -> HarnessContext:
        memory = MemoryAdapter(self.name, message)
        working = memory.get_working() or {}
        experience = memory.get_experience() or {}
        package = build_agent_context_package(
            agent_name=self.name,
            message=message,
            skill_name=self.skill.name,
            working=working,
            experience=experience,
            allowed_tools=self.skill.allowed_tools,
            policies=self.skill.policies,
        )
        return HarnessContext(
            message=message,
            skill=self.skill,
            config=self.config,
            tools=ToolGateway(self.skill.allowed_tools),
            memory=memory,
            artifacts=ArtifactAdapter(self.name, message),
            events=SafeEventEmitter(callback=self._event_callback),
            package=package,
            model=None,
            _model_loader=self._get_model_adapter,
        )

    def _get_model_adapter(self):
        try:
            from app.agents.model_adapter import get_model_adapter
            return get_model_adapter()
        except Exception:
            return None

    async def execute(self, ctx: HarnessContext) -> dict:
        raise NotImplementedError

    async def run(self, message: A2AMessage) -> dict:
        """Legacy hook — new roles must implement execute(ctx)."""
        raise NotImplementedError

    def _uses_legacy_run(self) -> bool:
        return type(self).execute is AgentHarness.execute and type(self).run is not AgentHarness.run

    async def handle(self, message: A2AMessage) -> dict:
        if message.target_agent != self.name:
            raise ValueError(f"A2A 目标与 Harness 不匹配: expected={self.name}, got={message.target_agent}")

        if self._uses_legacy_run():
            return await self._handle_legacy(message)
        return await self._handle_pipeline(message)

    async def _handle_legacy(self, message: A2AMessage) -> dict:
        ctx = self.build_context(message)
        ctx.events.emit("agent_lifecycle", {
            "agent": self.name,
            "status": "running",
            "task_id": message.task_id,
            "session_id": message.session_id,
            "user_id": message.user_id,
            "space_id": message.space_id,
        })
        try:
            result = await asyncio.wait_for(
                self._run_with_retry_legacy(message),
                timeout=self.config.timeout_seconds,
            )
            if self.skill.output_schema in ("QueryResult", "QueryRequest"):
                if not result.get("artifact_id"):
                    raise ValueError("Harness 输出缺少 artifact_id")
            else:
                result = OutputValidator(self.skill.output_schema).validate(result)
            ctx.events.emit("agent_lifecycle", {
                "agent": self.name,
                "status": "completed",
                "task_id": message.task_id,
                "artifact_id": result.get("artifact_id"),
            })
            return result
        except Exception as exc:
            ctx.events.emit("agent_lifecycle", {
                "agent": self.name,
                "status": "failed",
                "task_id": message.task_id,
                "error": type(exc).__name__,
            })
            raise

    async def _run_with_retry_legacy(self, message: A2AMessage) -> dict:
        last_err: Exception | None = None
        attempts = 1 + max(0, self.config.max_retries)
        for i in range(attempts):
            try:
                return await self.run(message)
            except Exception as e:
                last_err = e
                if i + 1 >= attempts:
                    break
                await asyncio.sleep(self.config.retry_backoff_seconds * (i + 1))
        assert last_err is not None
        raise last_err

    async def _handle_pipeline(self, message: A2AMessage) -> dict:
        ctx = self.build_context(message)
        ctx.events.emit("agent_lifecycle", {
            "agent": self.name,
            "status": "running",
            "task_id": message.task_id,
            "session_id": message.session_id,
            "user_id": message.user_id,
            "space_id": message.space_id,
            "context_ready": True,
        })
        try:
            result = await asyncio.wait_for(
                self._execute_with_retry(ctx),
                timeout=self.config.timeout_seconds,
            )
            result = OutputValidator(self.skill.output_schema).validate(result)
            ctx.events.emit("agent_lifecycle", {
                "agent": self.name,
                "status": "completed",
                "task_id": message.task_id,
                "artifact_id": result.get("artifact_id"),
            })
            return result
        except Exception as exc:
            ctx.events.emit("agent_lifecycle", {
                "agent": self.name,
                "status": "failed",
                "task_id": message.task_id,
                "error": type(exc).__name__,
            })
            raise

    async def _execute_with_retry(self, ctx: HarnessContext) -> dict:
        last_err: Exception | None = None
        attempts = 1 + max(0, ctx.config.max_retries)
        for i in range(attempts):
            try:
                return await self.execute(ctx)
            except Exception as e:
                last_err = e
                if i + 1 >= attempts:
                    break
                await asyncio.sleep(ctx.config.retry_backoff_seconds * (i + 1))
        assert last_err is not None
        raise last_err
