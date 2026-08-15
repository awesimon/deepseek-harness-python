"""Behavior tests for the public Python plugin authoring SDK."""

from __future__ import annotations

import asyncio
import inspect
import unittest
from dataclasses import FrozenInstanceError
from types import MappingProxyType

from harness.agent.values import InvalidJsonValueError
from harness.bridge import BROWSER_BRIDGE
from harness.cordis import FiberState, InactiveContextError, ServiceKey
from harness.plugins import PLUGIN_RUNTIME_IDENTITY
from harness.sdk import (
    BackendPluginChannel,
    BackendPluginContext,
    BridgeBackendPluginContext,
    client_event,
    define_backend_plugin,
    define_bridge_backend_plugin,
    rpc_method,
    server_event,
)
from harness.sdk.testing import BackendPluginHarness, FullStackPluginHarness


class PluginDescriptorTests(unittest.TestCase):
    """Protocol descriptors carry only immutable names and directions."""

    def test_descriptors_are_immutable_and_identity_free(self) -> None:
        """Descriptors cannot carry or later acquire Plugin identity."""
        method = rpc_method("describe")
        from_client = client_event("changed")
        from_server = server_event("render")

        self.assertEqual((method.name, method.direction), ("describe", "rpc"))
        self.assertEqual((from_client.name, from_client.direction), ("changed", "client"))
        self.assertEqual((from_server.name, from_server.direction), ("render", "server"))
        self.assertFalse(hasattr(method, "plugin_id"))
        self.assertFalse(hasattr(method, "revision"))
        with self.assertRaises(FrozenInstanceError):
            method.name = "retargeted"  # type: ignore[misc]

    def test_empty_descriptor_names_are_rejected(self) -> None:
        """Every wire operation requires an explicit non-empty name."""
        for factory in (rpc_method, client_event, server_event):
            with (
                self.subTest(factory=factory.__name__),
                self.assertRaisesRegex(ValueError, "must not be empty"),
            ):
                factory("")

    def test_production_factories_and_contexts_do_not_accept_identity(self) -> None:
        """Only Manager and test infrastructure can construct bound identity."""
        for factory in (define_backend_plugin, define_bridge_backend_plugin):
            parameters = inspect.signature(factory).parameters
            self.assertNotIn("plugin_id", parameters)
            self.assertNotIn("revision", parameters)
            with self.assertRaises(TypeError):
                factory(lambda _context: None, plugin_id="forbidden")  # type: ignore[call-arg]

        for context_type in (
            BackendPluginContext,
            BridgeBackendPluginContext,
            BackendPluginChannel,
        ):
            with (
                self.subTest(context_type=context_type.__name__),
                self.assertRaises(TypeError),
            ):
                context_type(plugin_id="forbidden", revision="forbidden")  # type: ignore[call-arg]

    def test_duplicate_and_sdk_owned_dependencies_are_rejected(self) -> None:
        """Generated specifications never contain ambiguous dependency entries."""
        service = ServiceKey[str]("tests.sdk.duplicate")
        with self.assertRaisesRegex(ValueError, "more than once"):
            define_backend_plugin(lambda _context: None, requires=(service, service))
        with self.assertRaisesRegex(ValueError, "SDK-owned"):
            define_backend_plugin(
                lambda _context: None,
                requires=(PLUGIN_RUNTIME_IDENTITY,),
            )
        with self.assertRaisesRegex(ValueError, "SDK-owned"):
            define_bridge_backend_plugin(
                lambda _context: None,
                requires=(BROWSER_BRIDGE,),
            )


class BackendPluginHarnessTests(unittest.IsolatedAsyncioTestCase):
    """Backend-only plugins use declared Services without Bridge dependencies."""

    async def test_backend_plugin_uses_injected_identity_and_owns_effects(self) -> None:
        """The SDK passes read-only identity and unloads custom Effects."""
        messages = ServiceKey[list[str]]("tests.sdk.messages")
        trace: list[str] = []

        async def setup(context: BackendPluginContext):
            self.assertEqual(context.plugin_id, "com.example.backend")
            self.assertEqual(context.revision, "fixture-revision")
            context.cordis.require(messages).append("started")
            await context.cordis.effect(
                lambda: lambda: trace.append("effect-cleaned"),
                "author-effect",
            )
            return lambda: trace.append("setup-cleaned")

        plugin = define_backend_plugin(setup, requires=(messages,), name="backend-example")
        self.assertEqual(plugin.requires, (messages, PLUGIN_RUNTIME_IDENTITY))
        self.assertNotIn(BROWSER_BRIDGE, plugin.requires)
        values: list[str] = []
        harness = BackendPluginHarness(
            plugin,
            plugin_id="com.example.backend",
            revision="fixture-revision",
            services={messages: values},
        )

        fiber = await harness.start()
        self.assertIs(fiber.state, FiberState.ACTIVE)
        self.assertEqual(values, ["started"])
        await asyncio.gather(harness.dispose(), harness.dispose())

        self.assertIs(fiber.state, FiberState.DISPOSED)
        self.assertCountEqual(trace, ["effect-cleaned", "setup-cleaned"])

    async def test_failed_setup_rolls_back_author_effects(self) -> None:
        """Setup failure retains diagnostics and removes effects from that attempt."""
        trace: list[str] = []

        async def setup(context: BackendPluginContext) -> None:
            await context.cordis.effect(
                lambda: lambda: trace.append("rolled-back"),
                "before-failure",
            )
            raise RuntimeError("setup failed")

        harness = BackendPluginHarness(
            define_backend_plugin(setup),
            plugin_id="com.example.failed",
            revision="failed-revision",
        )
        fiber = await harness.start()

        self.assertIs(fiber.state, FiberState.FAILED)
        self.assertIsInstance(fiber.error, RuntimeError)
        self.assertEqual(trace, ["rolled-back"])
        await harness.dispose()

    async def test_cleanup_failure_is_reported_without_repeating_cleanup(self) -> None:
        """Harness teardown reports Fiber diagnostics after attempting all disposal."""
        cleanup_count = 0

        def setup(_context: BackendPluginContext):
            def cleanup() -> None:
                nonlocal cleanup_count
                cleanup_count += 1
                raise RuntimeError("cleanup failed")

            return cleanup

        harness = BackendPluginHarness(
            define_backend_plugin(setup),
            plugin_id="com.example.cleanup",
            revision="cleanup-revision",
        )
        await harness.start()

        with self.assertRaisesRegex(BaseExceptionGroup, "plugin harness cleanup failed"):
            await harness.dispose()
        self.assertEqual(cleanup_count, 1)
        with self.assertRaises(BaseExceptionGroup):
            await harness.dispose()
        self.assertEqual(cleanup_count, 1)


class FullStackPluginHarnessTests(unittest.IsolatedAsyncioTestCase):
    """The full-stack harness exercises public Bridge authorization paths."""

    async def test_rpc_and_bidirectional_events_use_only_injected_identity(self) -> None:
        """Descriptors cannot retarget RPC or Events away from the active Revision."""
        echo = rpc_method("echo")
        changed = client_event("changed")
        rendered = server_event("rendered")
        channels: list[BackendPluginChannel] = []
        received: list[tuple[str, object, str]] = []
        argument_types: list[type[object]] = []

        async def setup(context: BridgeBackendPluginContext) -> None:
            channels.append(context.channel)

            def handle_rpc(arguments):
                argument_types.append(type(arguments))
                return {
                    "pluginId": context.plugin_id,
                    "revision": context.revision,
                    "value": arguments["value"],
                }

            await context.channel.register_rpc(echo, handle_rpc)
            await context.channel.on_client_event(
                changed,
                lambda page_id, payload: received.append((page_id, payload, context.revision)),
            )

        harness = FullStackPluginHarness(
            define_bridge_backend_plugin(setup),
            plugin_id="com.example.full-stack",
            revision="revision-1",
        )
        await harness.start()

        self.assertEqual(
            harness.rpc.active,
            {("com.example.full-stack", "revision-1", "echo")},
        )
        self.assertEqual(
            harness.events.active,
            {("com.example.full-stack", "revision-1", "changed")},
        )
        response = await harness.call_rpc(echo, {"value": 7})
        self.assertIsNone(response.error_code)
        self.assertEqual(
            response.result,
            {
                "pluginId": "com.example.full-stack",
                "revision": "revision-1",
                "value": 7,
            },
        )
        self.assertEqual(argument_types, [type(MappingProxyType({}))])

        await harness.send_client_event(changed, {"sequence": 1})
        self.assertEqual(
            received,
            [("test-page", {"sequence": 1}, "revision-1")],
        )
        delivered = await channels[0].emit_client_event(rendered, {"sequence": 2})
        self.assertEqual(delivered, 1)
        self.assertEqual(harness.emitted_events[-1].name, "rendered")
        self.assertEqual(harness.emitted_events[-1].payload, {"sequence": 2})

        await harness.dispose()
        harness.assert_no_registrations()
        with self.assertRaises(InactiveContextError):
            await channels[0].emit_client_event(rendered, {"sequence": 3})
        with self.assertRaises(InactiveContextError):
            await channels[0].register_rpc(echo, lambda arguments: arguments)

    async def test_duplicate_registration_failure_rolls_back_first_registration(self) -> None:
        """Duplicate names fail activation instead of replacing a handler."""
        method = rpc_method("duplicate")

        async def setup(context: BridgeBackendPluginContext) -> None:
            await context.channel.register_rpc(method, lambda arguments: arguments)
            await context.channel.register_rpc(method, lambda arguments: arguments)

        harness = FullStackPluginHarness(
            define_bridge_backend_plugin(setup),
            plugin_id="com.example.duplicate",
            revision="duplicate-revision",
        )
        fiber = await harness.start()

        self.assertIs(fiber.state, FiberState.FAILED)
        self.assertIsInstance(fiber.error, RuntimeError)
        harness.assert_no_registrations()
        await harness.dispose()

    async def test_cancellation_and_handler_failure_are_structured_results(self) -> None:
        """RPC cancellation and exceptions never become successful values."""
        wait = rpc_method("wait")
        fail = rpc_method("fail")
        started = asyncio.Event()

        async def wait_forever(_arguments):
            started.set()
            await asyncio.Event().wait()

        async def setup(context: BridgeBackendPluginContext) -> None:
            await context.channel.register_rpc(wait, wait_forever)
            await context.channel.register_rpc(
                fail,
                lambda _arguments: (_ for _ in ()).throw(RuntimeError("handler failed")),
            )

        harness = FullStackPluginHarness(
            define_bridge_backend_plugin(setup),
            plugin_id="com.example.rpc-errors",
            revision="rpc-errors-revision",
        )
        await harness.start()

        task = asyncio.create_task(harness.call_rpc(wait, {}, call_id="cancel-me"))
        await started.wait()
        self.assertTrue(harness.cancel_rpc("cancel-me"))
        cancelled = await task
        self.assertEqual(cancelled.error_code, "cancelled")
        failed = await harness.call_rpc(fail, {})
        self.assertEqual(failed.error_code, "handler_error")
        self.assertEqual(failed.error_message, "handler failed")
        await harness.dispose()

    async def test_non_json_values_fail_at_sdk_controlled_operations(self) -> None:
        """SDK operations reject unsupported objects without string conversion."""
        invalid_result = rpc_method("invalid-result")
        from_client = client_event("from-client")
        from_server = server_event("from-server")
        channels: list[BackendPluginChannel] = []

        async def setup(context: BridgeBackendPluginContext) -> None:
            channels.append(context.channel)
            await context.channel.register_rpc(
                invalid_result,
                lambda _arguments: {"not-json"},
            )
            await context.channel.on_client_event(from_client, lambda _page, _payload: None)

        harness = FullStackPluginHarness(
            define_bridge_backend_plugin(setup),
            plugin_id="com.example.json",
            revision="json-revision",
        )
        await harness.start()

        result = await harness.call_rpc(invalid_result, {})
        self.assertEqual(result.error_code, "handler_error")
        self.assertIn("unsupported JSON value", result.error_message or "")
        with self.assertRaises(InvalidJsonValueError):
            await harness.call_rpc(invalid_result, {"bad": object()})
        with self.assertRaises(InvalidJsonValueError):
            await harness.send_client_event(from_client, {"bad": object()})
        with self.assertRaises(InvalidJsonValueError):
            await channels[0].emit_client_event(from_server, {"bad": object()})
        await harness.dispose()


if __name__ == "__main__":
    unittest.main()
