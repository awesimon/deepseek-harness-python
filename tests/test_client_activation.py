"""Multi-page client activation aggregation and Manager readiness tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.bridge import (
    PROTOCOL_VERSION,
    BrowserBridge,
    PagePluginState,
    PluginLoadResult,
    ReconcileComplete,
    StaleBridgeMessageError,
)
from harness.cordis import Cordis
from harness.plugins import (
    ActivationPolicy,
    ClientActivationAggregator,
    ClientActivationSnapshot,
    ClientActivationState,
    ClientArtifactRegistry,
    ClientPageObservation,
    ClientQuorum,
    PluginManager,
    PluginState,
)

PLUGIN_ID = "com.example.client"
REVISION = "rev-1"


def observation(
    page_id: str,
    generation: int,
    *,
    state: str | None,
    revision: str | None = REVISION,
    complete: bool = False,
    error: str | None = None,
    operation_error: str | None = None,
) -> ClientPageObservation:
    """Build one current target observation for pure aggregation tests."""
    return ClientPageObservation(
        page_id,
        generation,
        "1",
        PLUGIN_ID,
        REVISION,
        revision,
        state,
        error,
        complete,
        operation_error,
    )


class ClientActivationAggregatorTests(unittest.TestCase):
    """Exercise quorum ordering, failures, recovery, and diagnostics."""

    def setUp(self) -> None:
        self.reports: list[ClientActivationSnapshot] = []
        self.aggregator = ClientActivationAggregator(
            lambda snapshot: not self.reports.append(snapshot)
        )
        self.aggregator.configure(
            PLUGIN_ID,
            ActivationPolicy.REQUIRED,
            ClientQuorum.ALL_CONNECTED,
        )
        self.aggregator.publish(PLUGIN_ID, REVISION)

    def test_empty_pending_failure_quorums_and_recovery(self) -> None:
        """Both quorum rules follow the normative aggregate precedence order."""
        self.assertIs(self.reports[-1].state, ClientActivationState.UNOBSERVED)

        self.aggregator.observe(
            {
                "page-b": (observation("page-b", 2, state=None),),
                "page-a": (observation("page-a", 1, state="active"),),
            }
        )
        pending = self.reports[-1]
        self.assertIs(pending.state, ClientActivationState.RECONCILING)
        self.assertEqual((pending.active_page_count, pending.pending_page_count), (1, 1))
        self.assertEqual(
            [item.page_id for item in pending.diagnostics],
            ["page-a", "page-b"],
        )

        self.aggregator.observe(
            {
                "page-a": (observation("page-a", 1, state="active"),),
                "page-b": (
                    observation(
                        "page-b",
                        2,
                        state="failed",
                        error="import failed",
                    ),
                ),
            }
        )
        failed = self.reports[-1]
        self.assertIs(failed.state, ClientActivationState.FAILED)
        self.assertEqual(failed.failed_page_count, 1)
        self.assertEqual(
            failed.diagnostics[1].error_code,
            "client_activation_failed",
        )

        self.aggregator.configure(
            PLUGIN_ID,
            ActivationPolicy.REQUIRED,
            ClientQuorum.ANY_CONNECTED,
        )
        self.assertIs(self.reports[-1].state, ClientActivationState.DEGRADED)

        self.aggregator.observe(
            {
                "page-a": (observation("page-a", 1, state="active"),),
                "page-b": (observation("page-b", 2, state="active"),),
            }
        )
        recovered = self.reports[-1]
        self.assertIs(recovered.state, ClientActivationState.ACTIVE)
        self.assertEqual(recovered.active_page_count, 2)

    def test_completed_missing_entry_and_operation_failure_are_terminal(self) -> None:
        """Settled unresolved target entries retain stable structured codes."""
        self.aggregator.observe(
            {
                "page-a": (
                    observation(
                        "page-a",
                        1,
                        state=None,
                        revision=None,
                        complete=True,
                    ),
                ),
                "page-b": (
                    observation(
                        "page-b",
                        2,
                        state=None,
                        revision=None,
                        complete=True,
                        operation_error="operation aborted",
                    ),
                ),
            }
        )
        snapshot = self.reports[-1]
        self.assertIs(snapshot.state, ClientActivationState.FAILED)
        self.assertEqual(
            [item.error_code for item in snapshot.diagnostics],
            ["client_revision_absent", "reconcile_operation_failed"],
        )


class BrowserMembershipGenerationTests(unittest.TestCase):
    """Prove exact inventory and replacement generation ownership."""

    def setUp(self) -> None:
        self.clients = ClientArtifactRegistry()
        self.clients.publish(PLUGIN_ID, REVISION, b"bundle")
        self.reports: list[ClientActivationSnapshot] = []
        self.aggregator = ClientActivationAggregator(
            lambda snapshot: not self.reports.append(snapshot)
        )
        self.aggregator.configure(
            PLUGIN_ID,
            ActivationPolicy.REQUIRED,
            ClientQuorum.ALL_CONNECTED,
        )
        self.aggregator.publish(PLUGIN_ID, REVISION)
        self.bridge = BrowserBridge(self.clients, aggregation=self.aggregator)

    def test_matching_inventory_is_active_and_disconnect_removes_membership(self) -> None:
        """Accepted exact-Revision hello inventory participates immediately."""
        self.bridge.connect("page-a", {PLUGIN_ID: REVISION})
        self.assertIs(self.reports[-1].state, ClientActivationState.ACTIVE)
        self.bridge.disconnect("page-a")
        self.assertIs(self.reports[-1].state, ClientActivationState.UNOBSERVED)

    def test_replaced_generation_cannot_report_complete_or_disconnect(self) -> None:
        """Every late mutation from the old logical connection is rejected."""
        old_operation = self.bridge.connect("page-a", {})
        old_generation = self.bridge.page_generation("page-a")
        self.reports.clear()
        self.bridge.connect("page-a", {})
        new_generation = self.bridge.page_generation("page-a")
        self.assertGreater(new_generation, old_generation)
        self.assertNotIn(
            ClientActivationState.UNOBSERVED,
            [snapshot.state for snapshot in self.reports],
        )

        with self.assertRaises(StaleBridgeMessageError):
            self.bridge.report(
                "page-a",
                PluginLoadResult(
                    PROTOCOL_VERSION,
                    old_operation.operation_id,
                    PLUGIN_ID,
                    REVISION,
                    PagePluginState.ACTIVE,
                ),
                generation=old_generation,
            )
        with self.assertRaises(StaleBridgeMessageError):
            self.bridge.complete(
                "page-a",
                ReconcileComplete(
                    PROTOCOL_VERSION,
                    old_operation.operation_id,
                    True,
                ),
                generation=old_generation,
            )
        with self.assertRaises(StaleBridgeMessageError):
            self.bridge.disconnect("page-a", generation=old_generation)

        self.assertEqual(self.bridge.page_generation("page-a"), new_generation)
        self.assertEqual(dict(self.bridge.page_snapshot("page-a")), {})


class PluginManagerClientReadinessTests(unittest.IsolatedAsyncioTestCase):
    """Exercise required and optional client state mapping through real authorities."""

    async def asyncSetUp(self) -> None:
        self.runtime = Cordis()
        self.clients = ClientArtifactRegistry()
        self.manager = PluginManager(self.runtime.root, clients=self.clients)
        self.aggregator = ClientActivationAggregator(self.manager.report_client_activation)
        self.manager.attach_client_aggregator(self.aggregator)
        self.bridge = BrowserBridge(self.clients, aggregation=self.aggregator)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self) -> None:
        await self.runtime.close()
        self.temp.cleanup()

    def _write_client(self, policy: str) -> None:
        (self.root / "client.js").write_bytes(b"client")
        (self.root / "plugin.toml").write_text(
            '[plugin]\nid = "com.example.client"\nversion = "1.0.0"\n'
            'runtime_api = "1"\n\n[client]\nbundle = "client.js"\nplatform = "web"\n\n'
            f'[activation]\nclient = "{policy}"\n',
            encoding="utf-8",
        )

    def _write_backend(self) -> None:
        (self.root / "backend.py").write_text(
            "from harness.cordis import PluginSpec\n"
            "async def apply(_ctx, _config):\n"
            "    return None\n"
            'plugin = PluginSpec("backend", apply)\n',
            encoding="utf-8",
        )
        (self.root / "plugin.toml").write_text(
            '[plugin]\nid = "com.example.client"\nversion = "1.1.0"\n'
            'runtime_api = "1"\n\n[backend]\nentrypoint = "backend.py:plugin"\n\n'
            '[activation]\nbackend = "required"\n',
            encoding="utf-8",
        )

    async def test_required_client_waits_fails_and_recovers_without_republish(self) -> None:
        """Post-publication browser readiness is recoverable Manager state."""
        self._write_client("required")
        await self.manager.install(self.root)
        enabled = await self.manager.enable(PLUGIN_ID)
        self.assertIs(enabled.state, PluginState.WAITING)
        revision = enabled.revision

        first = self.bridge.connect("page-a", {})
        self.bridge.report(
            "page-a",
            PluginLoadResult(
                PROTOCOL_VERSION,
                first.operation_id,
                PLUGIN_ID,
                revision,
                PagePluginState.ACTIVE,
            ),
        )
        self.assertIs(self.manager.snapshot()[PLUGIN_ID].state, PluginState.ACTIVE)

        second = self.bridge.connect("page-b", {})
        self.assertIs(self.manager.snapshot()[PLUGIN_ID].state, PluginState.WAITING)
        self.bridge.report(
            "page-b",
            PluginLoadResult(
                PROTOCOL_VERSION,
                second.operation_id,
                PLUGIN_ID,
                revision,
                PagePluginState.FAILED,
                "import failed",
            ),
        )
        self.assertIs(self.manager.snapshot()[PLUGIN_ID].state, PluginState.FAILED)

        self.bridge.report(
            "page-b",
            PluginLoadResult(
                PROTOCOL_VERSION,
                second.operation_id,
                PLUGIN_ID,
                revision,
                PagePluginState.ACTIVE,
            ),
        )
        recovered = self.manager.snapshot()[PLUGIN_ID]
        self.assertIs(recovered.state, PluginState.ACTIVE)
        self.assertEqual(recovered.client_revision, revision)

    async def test_optional_client_failure_degrades_client_only_plugin(self) -> None:
        """Optional unobserved client is ready but a settled failure is visible."""
        self._write_client("optional")
        await self.manager.install(self.root)
        enabled = await self.manager.enable(PLUGIN_ID)
        self.assertIs(enabled.state, PluginState.ACTIVE)

        operation = self.bridge.connect("page-a", {})
        self.bridge.report(
            "page-a",
            PluginLoadResult(
                PROTOCOL_VERSION,
                operation.operation_id,
                PLUGIN_ID,
                enabled.revision,
                PagePluginState.FAILED,
                "import failed",
            ),
        )
        degraded = self.manager.snapshot()[PLUGIN_ID]
        self.assertIs(degraded.state, PluginState.DEGRADED)
        self.assertIs(
            degraded.client_activation.state,
            ClientActivationState.FAILED,
        )

    async def test_disable_reports_draining_then_not_published(self) -> None:
        """Serving revocation is immediate while connected-page drainage remains visible."""
        self._write_client("required")
        await self.manager.install(self.root)
        enabled = await self.manager.enable(PLUGIN_ID)
        operation = self.bridge.connect("page-a", {})
        self.bridge.report(
            "page-a",
            PluginLoadResult(
                PROTOCOL_VERSION,
                operation.operation_id,
                PLUGIN_ID,
                enabled.revision,
                PagePluginState.ACTIVE,
            ),
        )

        disabled = await self.manager.disable(PLUGIN_ID)
        self.assertIs(disabled.state, PluginState.DISABLED)
        self.assertIs(
            disabled.client_activation.state,
            ClientActivationState.DRAINING,
        )
        self.assertIsNone(self.clients.current_revision(PLUGIN_ID))

        self.bridge.disconnect("page-a")
        settled = self.manager.snapshot()[PLUGIN_ID]
        self.assertIs(settled.state, PluginState.DISABLED)
        self.assertIs(
            settled.client_activation.state,
            ClientActivationState.NOT_PUBLISHED,
        )

    async def test_update_and_rollback_reconfigure_changed_contribution_form(self) -> None:
        """Client readiness follows the current Manifest when contribution form changes."""
        self._write_client("required")
        await self.manager.install(self.root)
        client = await self.manager.enable(PLUGIN_ID)
        operation = self.bridge.connect("page-a", {})
        self.bridge.report(
            "page-a",
            PluginLoadResult(
                PROTOCOL_VERSION,
                operation.operation_id,
                PLUGIN_ID,
                client.revision,
                PagePluginState.ACTIVE,
            ),
        )

        self._write_backend()
        backend = await self.manager.update(self.root)
        self.assertIs(backend.state, PluginState.ACTIVE)
        self.assertIs(
            backend.client_activation.state,
            ClientActivationState.NOT_APPLICABLE,
        )
        self.assertIsNone(backend.client_revision)

        restored = await self.manager.rollback(PLUGIN_ID)
        self.assertIs(restored.state, PluginState.ACTIVE)
        self.assertIs(
            restored.client_activation.state,
            ClientActivationState.ACTIVE,
        )
        self.assertEqual(restored.client_revision, client.revision)


if __name__ == "__main__":
    unittest.main()
