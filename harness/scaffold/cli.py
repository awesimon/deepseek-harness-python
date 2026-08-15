"""Command-line interface for plugin project scaffolding."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .generator import PluginKind, create_plugin, validate_plugin
from .sdk_asset import export_browser_sdk


def build_parser() -> argparse.ArgumentParser:
    """Return the parser shared by the script and module entrypoints."""
    parser = argparse.ArgumentParser(prog="deepseek-harness-plugin")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a plugin project")
    create.add_argument("--kind", choices=tuple(item.value for item in PluginKind), required=True)
    create.add_argument("--plugin-id", required=True)
    create.add_argument("--version", default="0.1.0")
    create.add_argument("--destination", type=Path, required=True)
    validate = commands.add_parser("validate", help="validate a generated plugin project")
    validate.add_argument("plugin_root", type=Path)
    sdk = commands.add_parser("sdk", help="export local authoring SDK artifacts")
    sdk_commands = sdk.add_subparsers(dest="sdk_command", required=True)
    export = sdk_commands.add_parser("export", help="export the Browser SDK tarball")
    export.add_argument("destination", type=Path)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one scaffolding command and return its process status."""
    namespace = build_parser().parse_args(arguments)
    try:
        if namespace.command == "create":
            destination = create_plugin(
                PluginKind(namespace.kind),
                namespace.plugin_id,
                namespace.destination,
                version=namespace.version,
            )
            print(destination)
        elif namespace.command == "validate":
            validate_plugin(namespace.plugin_root)
            print(Path(namespace.plugin_root).resolve())
        else:
            if namespace.sdk_command != "export":
                raise ValueError(f"unsupported SDK command: {namespace.sdk_command}")
            exported = export_browser_sdk(namespace.destination)
            print(exported.path)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"deepseek-harness-plugin: {error}", file=sys.stderr)
        return 2
    return 0
