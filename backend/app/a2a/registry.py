from collections.abc import Awaitable, Callable
from app.a2a.contracts import A2AMessage
AgentHandler = Callable[[A2AMessage], Awaitable[dict]]

class AgentRegistry:
    def __init__(self): self._handlers: dict[str, AgentHandler] = {}
    def register(self, name: str, handler: AgentHandler) -> None: self._handlers[name] = handler
    def get(self, name: str) -> AgentHandler:
        if name not in self._handlers: raise ValueError(f"未注册 Agent: {name}")
        return self._handlers[name]

registry = AgentRegistry()
