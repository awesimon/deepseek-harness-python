"""Keyless Agent Loop tests through a real PyCordis composition."""

from __future__ import annotations

import unittest
from dataclasses import replace

from harness.agent import (
    AGENT_LOOP,
    AGENT_POST_STEP,
    AGENT_PRE_STEP,
    LLM_REGISTRY,
    PROMPT_REGISTRY,
    SESSION_LOG,
    TOOL_REGISTRY,
    AgentScope,
    AgentSpineConfig,
    LLMAdapterProtocolError,
    LLMRoute,
    LLMRouteNotFoundError,
    MaximumStepsExceededError,
    ModelChunk,
    ModelChunkRecorded,
    ModelRequestRecorded,
    ModelResponse,
    PromptSection,
    Role,
    SessionProjector,
    StepFailed,
    Tool,
    ToolCall,
    ToolCallId,
    ToolExecutionCompleted,
    agent_spine_plugin,
)
from harness.cordis import Cordis, FiberState, PluginSpec

OBJECT_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    """Exercise durable ordering, Tool continuation, and Step snapshots."""

    async def asyncSetUp(self) -> None:
        self.runtime = Cordis()
        self.spine = await self.runtime.mount(
            agent_spine_plugin(),
            AgentSpineConfig("session-1"),
        )
        self.assertIs(self.spine.state, FiberState.ACTIVE)
        self.loop = self.runtime.root.lookup(AGENT_LOOP)
        self.log = self.runtime.root.lookup(SESSION_LOG)
        self.llms = self.runtime.root.lookup(LLM_REGISTRY)
        self.tools = self.runtime.root.lookup(TOOL_REGISTRY)
        self.prompts = self.runtime.root.lookup(PROMPT_REGISTRY)
        assert self.loop is not None
        assert self.log is not None
        assert self.llms is not None
        assert self.tools is not None
        assert self.prompts is not None
        self.scope = AgentScope()
        self.route = LLMRoute("fake", "model")

    async def asyncTearDown(self) -> None:
        await self.runtime.close()

    async def _mount_extensions(
        self,
        adapter,
        *,
        include_tool: bool = True,
        tool_disposers: list | None = None,
    ):
        async def apply(context, _config) -> None:
            llms = context.require(LLM_REGISTRY)
            tools = context.require(TOOL_REGISTRY)
            prompts = context.require(PROMPT_REGISTRY)
            await context.effect(lambda: llms.register(self.route, adapter), "fake-llm")
            await context.effect(
                lambda: prompts.register(
                    PromptSection("identity", 10, lambda _context: "You are deterministic."),
                    scope=self.scope,
                ),
                "prompt",
            )
            if include_tool:
                def register_tool():
                    dispose = tools.register(
                        Tool(
                            "echo",
                            "Return the supplied value.",
                            OBJECT_SCHEMA,
                            lambda arguments: {"echo": arguments["value"]},
                        ),
                        scope=self.scope,
                    )
                    if tool_disposers is not None:
                        tool_disposers.append(dispose)
                    return dispose

                await context.effect(
                    register_tool,
                    "echo-tool",
                )

        return await self.runtime.mount(
            PluginSpec(
                "test-agent-extensions",
                apply,
                requires=(LLM_REGISTRY, TOOL_REGISTRY, PROMPT_REGISTRY),
            ),
            None,
        )

    async def test_keyless_tool_turn_records_replayable_transcript(self) -> None:
        """A real composition logs request, chunks, Tool result, and final response."""
        test = self

        class Adapter:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                test.assertIsInstance(test.log.snapshot()[-1].event, ModelRequestRecorded)
                test.assertEqual(request.system_prompt, "You are deterministic.")
                if self.calls == 1:
                    yield ModelChunk({"text": "calling"})
                    yield ModelResponse(
                        "",
                        (
                            ToolCall(
                                ToolCallId("call-1"),
                                "echo",
                                {"value": "hello"},
                            ),
                        ),
                        "tool_calls",
                    )
                else:
                    test.assertEqual(request.messages[-1].role, Role.TOOL)
                    yield ModelResponse("echoed hello")

        extension = await self._mount_extensions(Adapter())
        result = await self.loop.run_text(
            "echo hello",
            route=self.route,
            scope=self.scope,
        )

        self.assertEqual(result.message.content, "echoed hello")
        self.assertEqual(result.steps, 2)
        transcript = SessionProjector(self.log).transcript()
        self.assertEqual(
            [entry.kind for entry in transcript],
            ["user", "assistant", "tool", "assistant"],
        )
        self.assertEqual(
            sum(isinstance(item.event, ToolExecutionCompleted) for item in self.log.snapshot()),
            1,
        )
        self.assertEqual(
            sum(isinstance(item.event, ModelChunkRecorded) for item in self.log.snapshot()),
            1,
        )
        await extension.dispose()
        with self.assertRaises(LLMRouteNotFoundError):
            self.llms.resolve(self.route)
        self.assertNotIn("echo", self.tools.snapshot(self.scope))

    async def test_unknown_tool_error_is_visible_to_the_next_step(self) -> None:
        """Unknown calls become structured Tool Messages instead of Loop exceptions."""
        test = self

        class Adapter:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                if self.calls == 1:
                    yield ModelResponse(
                        "",
                        (ToolCall(ToolCallId("missing-1"), "missing", {}),),
                        "tool_calls",
                    )
                else:
                    test.assertIn('"code":"unknown_tool"', request.messages[-1].content)
                    yield ModelResponse("recovered")

        await self._mount_extensions(Adapter(), include_tool=False)
        result = await self.loop.run_text("try", route=self.route, scope=self.scope)
        self.assertEqual(result.message.content, "recovered")

    async def test_invalid_arguments_are_logged_for_the_next_step(self) -> None:
        """JSON Schema failures become structured Tool Messages."""
        test = self

        class Adapter:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                if self.calls == 1:
                    yield ModelResponse(
                        "",
                        (ToolCall(ToolCallId("invalid-1"), "echo", {}),),
                        "tool_calls",
                    )
                else:
                    test.assertIn('"code":"invalid_arguments"', request.messages[-1].content)
                    yield ModelResponse("recovered")

        await self._mount_extensions(Adapter())
        result = await self.loop.run_text("try", route=self.route, scope=self.scope)
        self.assertEqual(result.message.content, "recovered")

    async def test_agent_step_events_transform_then_observe_logged_response(self) -> None:
        """Pre-Step middleware runs before logging and post-Step runs after commit."""
        observed: list[str] = []
        test = self

        class Adapter:
            async def stream(self, request):
                test.assertTrue(request.system_prompt.endswith("Policy applied."))
                test.assertEqual(
                    test.log.snapshot()[-1].event.request.system_prompt,
                    request.system_prompt,
                )
                yield ModelResponse("done")

        async def add_policy(_request, next_call):
            request = await next_call()
            return replace(
                request,
                system_prompt=f"{request.system_prompt}\n\nPolicy applied.",
            )

        def observe(_request, response) -> None:
            test.assertEqual(test.log.snapshot()[-1].event.message, response.message)
            observed.append(response.content)

        await self.runtime.root.on(AGENT_PRE_STEP, add_policy)
        await self.runtime.root.on(AGENT_POST_STEP, observe)
        await self._mount_extensions(Adapter(), include_tool=False)

        await self.loop.run_text("run", route=self.route, scope=self.scope)
        self.assertEqual(observed, ["done"])

    async def test_tool_snapshot_survives_disposal_during_model_request(self) -> None:
        """The current Step keeps its Tool handler while the next snapshot loses it."""
        test = self
        tool_disposers: list = []

        class Adapter:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                if self.calls == 1:
                    test.assertEqual([tool.name for tool in request.tools], ["echo"])
                    tool_disposers[0]()
                    yield ModelResponse(
                        "",
                        (ToolCall(ToolCallId("call-1"), "echo", {"value": "kept"}),),
                        "tool_calls",
                    )
                else:
                    test.assertEqual(request.tools, ())
                    test.assertIn('"ok":true', request.messages[-1].content)
                    yield ModelResponse("done")

        await self._mount_extensions(Adapter(), tool_disposers=tool_disposers)
        result = await self.loop.run_text("run", route=self.route, scope=self.scope)
        self.assertEqual(result.message.content, "done")

    async def test_maximum_steps_records_failure_after_tool_outcome(self) -> None:
        """Step exhaustion preserves the Tool result and appends a failure Event."""

        class Adapter:
            async def stream(self, _request):
                yield ModelResponse(
                    "",
                    (ToolCall(ToolCallId("missing-1"), "missing", {}),),
                    "tool_calls",
                )

        await self._mount_extensions(Adapter(), include_tool=False)
        with self.assertRaises(MaximumStepsExceededError):
            await self.loop.run_text(
                "loop",
                route=self.route,
                scope=self.scope,
                max_steps=1,
            )
        self.assertIsInstance(self.log.snapshot()[-1].event, StepFailed)

    async def test_adapter_protocol_failure_is_logged(self) -> None:
        """Repeated terminal responses fail the Step without an invented message."""

        class Adapter:
            async def stream(self, _request):
                yield ModelResponse("first")
                yield ModelResponse("second")

        await self._mount_extensions(Adapter(), include_tool=False)
        with self.assertRaises(LLMAdapterProtocolError):
            await self.loop.run_text("run", route=self.route, scope=self.scope)
        failure = self.log.snapshot()[-1].event
        self.assertIsInstance(failure, StepFailed)
        self.assertEqual(failure.code, "adapter_protocol")
