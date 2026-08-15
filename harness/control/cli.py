"""JSON command-line client for the loopback Plugin Control API."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from aiohttp import ClientError, ClientResponse, ClientSession


def build_plugin_parser() -> argparse.ArgumentParser:
    """Return the parser for `deepseek-harness-python plugin`."""
    parser = argparse.ArgumentParser(prog="deepseek-harness-python plugin")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    commands = parser.add_subparsers(dest="plugin_command", required=True)
    commands.add_parser("list")
    show = commands.add_parser("show")
    show.add_argument("plugin_id")
    install = commands.add_parser("install")
    install.add_argument("plugin_root", type=Path)
    for name in ("enable", "disable", "update", "rollback", "uninstall"):
        command = commands.add_parser(name)
        command.add_argument("plugin_id")
        command.add_argument("--revision")
        command.add_argument("--mutation-version", type=int)
        if name == "rollback":
            command.add_argument("--target-revision")
    return parser


async def run_plugin_cli(namespace: argparse.Namespace) -> int:
    """Run one control command without constructing a local Manager."""
    base = f"{namespace.url.rstrip('/')}/api/control/v1/plugins"
    try:
        async with ClientSession() as client:
            command = namespace.plugin_command
            if command == "list":
                return await _print_response(await client.get(base))
            if command == "show":
                return await _print_response(await client.get(f"{base}/{namespace.plugin_id}"))
            if command == "install":
                response = await client.post(
                    f"{base}/install",
                    json={
                        "pluginRoot": str(namespace.plugin_root.resolve()),
                        "expectedAbsent": True,
                    },
                )
                return await _print_response(response, mutation=True)

            plugin_id = namespace.plugin_id
            snapshot = await _precondition_snapshot(client, base, plugin_id, namespace)
            if isinstance(snapshot, int):
                return snapshot
            body: dict[str, object] = {
                "expectedRevision": snapshot[0],
                "expectedMutationVersion": snapshot[1],
            }
            if command == "rollback":
                target = namespace.target_revision or snapshot[2]
                if not isinstance(target, str) or not target:
                    print(
                        "rollback_unavailable: plugin has no retained previous Revision",
                        file=sys.stderr,
                    )
                    return 1
                body["targetRevision"] = target
            response = await client.post(f"{base}/{plugin_id}/{command}", json=body)
            return await _print_response(response, mutation=True)
    except (ClientError, TimeoutError) as error:
        print(f"control_transport: {error}", file=sys.stderr)
        return 1


async def _precondition_snapshot(
    client: ClientSession,
    base: str,
    plugin_id: str,
    namespace: argparse.Namespace,
) -> tuple[str, int, str | None] | int:
    revision = namespace.revision
    version = namespace.mutation_version
    if (revision is None) != (version is None):
        print(
            "invalid_precondition: --revision and --mutation-version must appear together",
            file=sys.stderr,
        )
        return 2
    response = await client.get(f"{base}/{plugin_id}")
    payload = await _response_json(response)
    if response.status != 200:
        _print_error(response.status, payload)
        return 1
    if not isinstance(payload, Mapping):
        print("invalid_response: control snapshot is not an object", file=sys.stderr)
        return 1
    raw = cast(Mapping[object, object], payload)
    current_revision = revision if revision is not None else raw.get("revision")
    current_version = version if version is not None else raw.get("mutationVersion")
    previous = raw.get("previousRevision")
    if (
        not isinstance(current_revision, str)
        or not isinstance(current_version, int)
        or isinstance(current_version, bool)
        or (previous is not None and not isinstance(previous, str))
    ):
        print("invalid_response: control snapshot omitted preconditions", file=sys.stderr)
        return 1
    return current_revision, current_version, previous


async def _print_response(response: ClientResponse, *, mutation: bool = False) -> int:
    payload = await _response_json(response)
    if response.status < 200 or response.status >= 300:
        _print_error(response.status, payload)
        if isinstance(payload, Mapping):
            raw = cast(Mapping[object, object], payload)
            if raw.get("current") is not None:
                print(json.dumps(raw["current"], sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if mutation and isinstance(payload, Mapping):
        raw = cast(Mapping[object, object], payload)
        if raw.get("outcome") == "failed":
            print("operation_failed: plugin lifecycle operation failed", file=sys.stderr)
            return 1
    return 0


async def _response_json(response: ClientResponse) -> object:
    async with response:
        try:
            return cast(object, await response.json())
        except (ClientError, ValueError, TypeError):
            return None


def _print_error(status: int, payload: object) -> None:
    if isinstance(payload, Mapping):
        raw = cast(Mapping[object, object], payload)
        code = raw.get("code")
        message = raw.get("message")
        if isinstance(code, str) and isinstance(message, str):
            print(f"{code}: {message}", file=sys.stderr)
            return
    print(f"control_http: Host returned HTTP {status}", file=sys.stderr)


def parse_plugin_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    """Parse a standalone plugin-client argument sequence."""
    return build_plugin_parser().parse_args(arguments)
