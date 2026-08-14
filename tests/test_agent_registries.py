"""Behavior tests for scoped registries and LLM stream enforcement."""

from __future__ import annotations

import unittest

from deepseek_harness.agent import (
    AgentScope,
    DuplicateContributionError,
    LayeredRegistry,
    LLMAdapterProtocolError,
    LLMRegistry,
    LLMRoute,
    LLMRouteNotFoundError,
    ModelRequest,
    ModelResponse,
    StepId,
    TurnId,
)
from deepseek_harness.agent.llm import collect_adapter_response


class AgentRegistryTests(unittest.IsolatedAsyncioTestCase):
    """Exercise Scope precedence, disposal, routing, and adapter terminals."""

    def test_nearest_scope_wins_without_deleting_farther_values(self) -> None:
        """Exact contributions shadow ancestors only for descendant reads."""
        registry = LayeredRegistry[str]("example")
        parent = AgentScope()
        child = AgentScope(parent)
        registry.register("name", "global")
        dispose_parent = registry.register("name", "parent", scope=parent)
        registry.register("name", "child", scope=child)

        self.assertEqual(registry.snapshot(parent)["name"], "parent")
        self.assertEqual(registry.snapshot(child)["name"], "child")
        dispose_parent()
        self.assertEqual(registry.snapshot(parent)["name"], "global")
        self.assertEqual(registry.snapshot(child)["name"], "child")

    def test_duplicate_contribution_fails_in_the_same_layer(self) -> None:
        """One layer cannot contain two contributions with the same name."""
        registry = LayeredRegistry[str]("example")
        registry.register("name", "first")
        with self.assertRaises(DuplicateContributionError):
            registry.register("name", "second")

    async def test_llm_routes_are_explicit_and_effect_compatible(self) -> None:
        """Disposal removes one exact Route without selecting a fallback."""

        class Adapter:
            async def stream(self, _request):
                yield ModelResponse("done")

        route = LLMRoute("fake", "model")
        registry = LLMRegistry()
        adapter = Adapter()
        dispose = registry.register(route, adapter)
        self.assertIs(registry.resolve(route), adapter)
        dispose()
        with self.assertRaises(LLMRouteNotFoundError):
            registry.resolve(route)

    async def test_adapter_requires_exactly_one_terminal_response(self) -> None:
        """Missing and repeated terminal responses are protocol failures."""
        request = ModelRequest(
            TurnId("turn-1"),
            StepId("step-1"),
            LLMRoute("fake", "model"),
            "",
            (),
            (),
        )

        class Missing:
            async def stream(self, _request):
                if False:
                    yield ModelResponse("unreachable")

        class Repeated:
            async def stream(self, _request):
                yield ModelResponse("first")
                yield ModelResponse("second")

        with self.assertRaises(LLMAdapterProtocolError):
            await collect_adapter_response(Missing(), request, lambda _chunk: None)
        with self.assertRaises(LLMAdapterProtocolError):
            await collect_adapter_response(Repeated(), request, lambda _chunk: None)
