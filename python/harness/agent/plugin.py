"""PyCordis Service composition for one in-process Agent Spine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from harness.cordis import Context, PluginSpec, ServiceKey

from .llm import LLMRegistry
from .loop import AgentLoop
from .persistence import SQLiteSessionStore
from .registries import PromptRegistry, ToolRegistry
from .session import SessionLog
from .values import SessionId

SESSION_LOG = ServiceKey[SessionLog]("agent.session-log")
PROMPT_REGISTRY = ServiceKey[PromptRegistry]("agent.prompt-registry")
TOOL_REGISTRY = ServiceKey[ToolRegistry]("agent.tool-registry")
LLM_REGISTRY = ServiceKey[LLMRegistry]("agent.llm-registry")
AGENT_LOOP = ServiceKey[AgentLoop]("agent.loop")


@dataclass(frozen=True, slots=True)
class AgentSpineConfig:
    """Configuration for one Session-scoped Agent Spine."""

    session_id: str
    session_db: Path | None = None

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session id must not be empty")


def agent_spine_plugin() -> PluginSpec[AgentSpineConfig]:
    """Return the plugin that provides all Phase 2 Agent Services."""

    async def apply(context: Context, config: AgentSpineConfig) -> None:
        store = None if config.session_db is None else SQLiteSessionStore(config.session_db)
        try:
            log = SessionLog(SessionId(config.session_id), store)
        except BaseException:
            if store is not None:
                store.close()
            raise
        prompts = PromptRegistry()
        tools = ToolRegistry()
        llms = LLMRegistry()
        loop = AgentLoop(context, log, prompts, tools, llms)
        await context.provide(SESSION_LOG, log)
        await context.provide(PROMPT_REGISTRY, prompts)
        await context.provide(TOOL_REGISTRY, tools)
        await context.provide(LLM_REGISTRY, llms)
        await context.provide(AGENT_LOOP, loop)
        await context.effect(lambda: log.close, "session-store-lifecycle")

    return PluginSpec("agent-spine", apply)
