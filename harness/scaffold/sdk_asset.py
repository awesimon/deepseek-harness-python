"""Validated Browser SDK tarball bundled with the Python distribution."""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from io import BytesIO
from pathlib import Path
from typing import cast

from harness.sdk import BROWSER_SDK_PACKAGE, BROWSER_SDK_VERSION

BROWSER_SDK_TARBALL = (
    f"deepseek-harness-browser-bridge-client-{BROWSER_SDK_VERSION}.tgz"
)
BROWSER_SDK_SHA256 = "1368da38333ad2794b9b27cdf13e3f18fea7e83be3fda5d84a209d648b6edc9e"
_CLIENT_LOCKFILE = "client-pnpm-lock.yaml"


@dataclass(frozen=True, slots=True)
class BrowserSdkExport:
    """Verified local SDK artifact copied for one caller."""

    path: Path
    version: str
    sha256: str


def browser_sdk_bytes() -> bytes:
    """Return the bundled tarball after digest and package metadata validation."""
    source = files("harness.assets").joinpath(BROWSER_SDK_TARBALL)
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != BROWSER_SDK_SHA256:
        raise RuntimeError("bundled Browser SDK digest does not match distribution metadata")
    try:
        with tarfile.open(fileobj=BytesIO(content), mode="r:gz") as archive:
            package_file = archive.extractfile("package/package.json")
            if package_file is None:
                raise RuntimeError("bundled Browser SDK omits package.json")
            package = json.loads(package_file.read())
    except (json.JSONDecodeError, tarfile.TarError) as error:
        raise RuntimeError("bundled Browser SDK is not a valid npm tarball") from error
    if not isinstance(package, dict):
        raise RuntimeError(  # noqa: TRY004 -- installed artifact corruption is operational
            "bundled Browser SDK package.json must be an object"
        )
    raw_package = cast(dict[object, object], package)
    if (
        raw_package.get("name") != BROWSER_SDK_PACKAGE
        or raw_package.get("version") != BROWSER_SDK_VERSION
    ):
        raise RuntimeError("bundled Browser SDK identity does not match authoring constants")
    return content


def browser_sdk_lockfile() -> str:
    """Return the frozen pnpm lockfile matching the bundled tarball dependency."""
    return files("harness.assets").joinpath(_CLIENT_LOCKFILE).read_text(encoding="utf-8")


def export_browser_sdk(destination: str | Path) -> BrowserSdkExport:
    """Copy the verified tarball without replacing different existing bytes."""
    content = browser_sdk_bytes()
    target = Path(destination)
    parent = target.parent.resolve(strict=True)
    resolved = parent / target.name
    if resolved.exists():
        if (
            resolved.is_file()
            and hashlib.sha256(resolved.read_bytes()).hexdigest() == BROWSER_SDK_SHA256
        ):
            return BrowserSdkExport(resolved, BROWSER_SDK_VERSION, BROWSER_SDK_SHA256)
        raise FileExistsError(f"destination already exists with different content: {resolved}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=parent))
    artifact = temporary / target.name
    try:
        artifact.write_bytes(content)
        os.link(artifact, resolved)
    finally:
        if artifact.exists():
            artifact.unlink()
        temporary.rmdir()
    return BrowserSdkExport(resolved, BROWSER_SDK_VERSION, BROWSER_SDK_SHA256)
