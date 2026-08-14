"""Durable Turn and Step orchestration over plugin-contributed capabilities."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from jsonschema import ValidationError

from deepseek_harness.cordis import Context, EventKey, EventMode

from .llm import LLMAdapterProtocolError, LLMRegistry, collect_adapter_response
from .registries import PromptRegistry, Tool, ToolRegistry
from .scope import AgentScope
from .session import (
    AssistantMessageCommitted,
    ModelChunkRecorded,
    ModelRequestRecorded,
    SessionLog,
    SessionProjector,
    StepFailed,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    UserInputAccepted,
)
from .values import (
    LLMRoute,
    Message,
    ModelChunk,
    ModelRequest,
    Role,
    StepId,
    ToolCall,
    ToolError,
    ToolInvocation,
    ToolOutcome,
    TurnId,
    freeze_json,
)

AGENT_PRE_STEP = EventKey[ModelRequest]("agent/pre-step", EventMode.WATERFALL)
AGENT_POST_STEP = EventKey[None]("agent/post-step", EventMode.PARALLEL)
TOOLS_PRE_EXECUTE = EventKey[ToolInvocation]("tools/pre-execute", EventMode.WATERFALL)
TOOLS_POST_EXECUTE = EventKey[None]("tools/post-execute", EventMode.PARALLEL)


class AgentLoopError(RuntimeError):
    """Base class for Agent Loop failures."""


class MaximumStepsExceededError(AgentLoopError):
    """Raised after a Turn consumes its configured Step budget."""


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Terminal result of one completed Agent Turn."""

    turn_id: TurnId
    message: Message
    steps: int


class AgentLoop:
    """Thin durable orchestration over Session, Prompt, Tool, and LLM Services."""

    def __init__(
        self,
        context: Context,
        log: SessionLog,
        prompts: PromptRegistry,
        tools: ToolRegistry,
        llms: LLMRegistry,
    ) -> None:
        self.context = context
        self.log = log
        self.projector = SessionProjector(log)
        self.prompts = prompts
        self.tools = tools
        self.llms = llms
        self._next_turn = 1

    async def run_text(
        self,
        text: str,
        *,
        route: LLMRoute,
        scope: AgentScope,
        max_steps: int = 8,
    ) -> AgentRunResult:
        """Run one Turn from a single User Message."""
        return await self.run(
            (Message(Role.USER, text),),
            route=route,
            scope=scope,
            max_steps=max_steps,
        )

    async def run(
        self,
        messages: Sequence[Message],
        *,
        route: LLMRoute,
        scope: AgentScope,
        max_steps: int = 8,
    ) -> AgentRunResult:
        """Run one durable Turn until a response has no Tool Calls."""
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        accepted = tuple(messages)
        if not accepted or any(message.role is not Role.USER for message in accepted):
            raise ValueError("a Turn requires one or more User Messages")

        turn_id = TurnId(f"turn-{self._next_turn}")
        self._next_turn += 1
        self.log.append(UserInputAccepted(turn_id, accepted))
        last_step_id: StepId | None = None

        for step_number in range(1, max_steps + 1):
            step_id = StepId(f"{turn_id}:step-{step_number}")
            last_step_id = step_id
            history = self.projector.model_history()
            system_prompt = await self.prompts.render(scope, history)
            tool_snapshot = self.tools.snapshot(scope)
            request = ModelRequest(
                turn_id,
                step_id,
                route,
                system_prompt,
                history,
                tuple(
                    tool.model_definition
                    for _, tool in sorted(tool_snapshot.items(), key=lambda item: item[0])
                ),
            )
            request = await self.context.waterfall(
                AGENT_PRE_STEP,
                request,
                terminal=_identity_request,
            )
            if request.turn_id != turn_id or request.step_id != step_id:
                raise AgentLoopError("agent/pre-step cannot replace Turn or Step identity")
            adapter = self.llms.resolve(request.route)
            self.log.append(ModelRequestRecorded(request))

            def record_chunk(chunk: ModelChunk, current_step: StepId = step_id) -> None:
                self.log.append(ModelChunkRecorded(current_step, chunk))

            try:
                response = await collect_adapter_response(adapter, request, record_chunk)
            except asyncio.CancelledError:
                self.log.append(StepFailed(step_id, "cancelled", "model request cancelled"))
                raise
            except Exception as error:
                code = (
                    "adapter_protocol"
                    if isinstance(error, LLMAdapterProtocolError)
                    else "adapter_error"
                )
                self.log.append(StepFailed(step_id, code, str(error)))
                raise

            message = response.message
            self.log.append(
                AssistantMessageCommitted(step_id, message, response.finish_reason)
            )
            await self.context.parallel(AGENT_POST_STEP, request, response)
            if not response.tool_calls:
                return AgentRunResult(turn_id, message, step_number)

            for call in response.tool_calls:
                await self._execute_tool(step_id, call, tool_snapshot)

        assert last_step_id is not None
        message = f"Turn {turn_id!r} reached its maximum of {max_steps} Steps"
        self.log.append(StepFailed(last_step_id, "maximum_steps", message))
        raise MaximumStepsExceededError(message)

    async def _execute_tool(
        self,
        step_id: StepId,
        call: ToolCall,
        tool_snapshot: Mapping[str, Tool],
    ) -> ToolOutcome:
        invocation = ToolInvocation(step_id, call)
        invocation = await self.context.waterfall(
            TOOLS_PRE_EXECUTE,
            invocation,
            terminal=_identity_invocation,
        )
        if invocation.step_id != step_id or invocation.call.id != call.id:
            raise AgentLoopError(
                "tools/pre-execute cannot replace Step or Tool Call identity"
            )
        self.log.append(ToolExecutionStarted(invocation.step_id, invocation.call))
        tool = tool_snapshot.get(invocation.call.name)
        if tool is None:
            outcome = ToolOutcome(
                invocation.step_id,
                invocation.call,
                error=ToolError(
                    "unknown_tool",
                    f"tool {invocation.call.name!r} is not available in this Step",
                ),
            )
            return await self._complete_tool(outcome)

        try:
            tool.validate(invocation.call.arguments)
            result = tool.handler(invocation.call.arguments)
            if inspect.isawaitable(result):
                result = await result
            outcome = ToolOutcome(
                invocation.step_id,
                invocation.call,
                result=freeze_json(result),
            )
        except asyncio.CancelledError:
            outcome = ToolOutcome(
                invocation.step_id,
                invocation.call,
                error=ToolError("cancelled", "tool execution cancelled"),
            )
            await self._complete_tool(outcome)
            raise
        except ValidationError as error:
            outcome = ToolOutcome(
                invocation.step_id,
                invocation.call,
                error=ToolError("invalid_arguments", error.message),
            )
        except Exception as error:  # noqa: BLE001 -- Tool failures become model-visible outcomes
            outcome = ToolOutcome(
                invocation.step_id,
                invocation.call,
                error=ToolError("handler_error", str(error)),
            )
        return await self._complete_tool(outcome)

    async def _complete_tool(self, outcome: ToolOutcome) -> ToolOutcome:
        self.log.append(ToolExecutionCompleted(outcome))
        await self.context.parallel(TOOLS_POST_EXECUTE, outcome)
        return outcome


def _identity_request(request: ModelRequest) -> ModelRequest:
    """Return the unmodified request when pre-Step middleware delegates."""
    return request


def _identity_invocation(invocation: ToolInvocation) -> ToolInvocation:
    """Return the unmodified Tool invocation when middleware delegates."""
    return invocation
