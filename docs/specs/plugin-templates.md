# Plugin Templates and Scaffolding Specification

Status: Phase 7 normative specification

## Purpose

Plugin Templates and Scaffolding provide the shortest supported path from a Plugin ID to a runnable backend-only, client-only, or full-stack plugin project. Generated projects use the Phase 6 SDK, preserve one root identity, and include keyless checks that exercise their declared contribution form.

## Scope

Phase 7 includes three built-in templates, a deterministic generator, a validation command, root manifest generation, local build configuration, example plugin behavior, keyless template tests, and overwrite-safe filesystem writes.

The templates target trusted local plugin catalogs consumed by the Dynamic Plugin Manager. They teach current SDK entrypoints and lifecycle ownership without introducing another runtime abstraction.

## Command interface

The distribution provides `deepseek-harness-plugin` and the equivalent `python -m harness.scaffold` module entrypoint. Both use the same parser and support these commands:

```sh
deepseek-harness-plugin create \
  --kind full-stack \
  --plugin-id com.example.echo \
  --destination plugins/echo

deepseek-harness-plugin validate plugins/echo
```

`create` requires `--kind`, `--plugin-id`, and `--destination`. `--kind` accepts only `backend`, `client`, or `full-stack`; `--version` defaults to `0.1.0`. The generator validates every input before creating a directory and prints the created plugin root on success.

`validate` accepts one plugin root, parses its root manifest through the same public validation used by the Dynamic Plugin Manager, derives the contribution form from that manifest, checks the corresponding template source files, and requires every manifest-declared runtime artifact to exist. A newly generated client or full-stack project therefore becomes runtime-valid after its documented frontend build command produces `frontend/dist/client.js`.

Usage and input errors return a nonzero status and write one actionable diagnostic to standard error. Neither command starts a Host, installs a plugin, invokes a package manager, or downloads dependencies.

## Identity and inputs

The generated root `plugin.toml` is the only authority for Plugin ID, semantic version, runtime API, contribution entrypoints, protocol artifacts, and activation policy. The Plugin ID and version are written exactly once as runtime metadata.

Nested `pyproject.toml` and `frontend/package.json` files contain only build and dependency metadata. Tooling names are deterministically derived from the Plugin ID, but they do not declare or override runtime identity. Templates never accept, calculate, persist, or pass a runtime Revision.

The generator accepts Plugin IDs and versions only when the Dynamic Plugin Manager's manifest validators accept them. It obtains the emitted `runtime_api` and SDK dependency versions from the installed Harness SDK rather than maintaining template-local compatibility constants.

## Generated layouts

Every template includes `.gitignore`, `README.md`, and `plugin.toml`. Generated prose identifies the exact build, test, validation, and Host catalog commands for that contribution form.

The backend-only template has this layout:

```text
.gitignore
README.md
plugin.toml
pyproject.toml
backend/
  plugin.py
tests/
  test_backend.py
```

Its manifest declares `backend/plugin.py:plugin` and no client or protocol contribution. The Python project declares the Harness SDK dependency needed for local development, while the runtime continues to load the manifest entrypoint directly.

The client-only template has this layout:

```text
.gitignore
README.md
plugin.toml
frontend/
  package.json
  pnpm-lock.yaml
  tsconfig.json
  src/
    plugin.ts
  tests/
    plugin.test.ts
```

Its manifest declares `frontend/dist/client.js` and no backend or protocol contribution. The frontend build emits that one ESM Bundle, and `.gitignore` excludes `frontend/dist/`.

The full-stack template combines both layouts and adds a shared protocol artifact:

```text
.gitignore
README.md
plugin.toml
pyproject.toml
backend/
  plugin.py
frontend/
  package.json
  pnpm-lock.yaml
  tsconfig.json
  src/
    plugin.ts
  tests/
    plugin.test.ts
protocol/
  api.schema.json
tests/
  test_backend.py
```

Its manifest declares backend, client, and protocol paths. Both contributions are `required` so the generated example cannot appear healthy with only one side active.

## SDK usage

Phase 7 depends on the [Phase 6 Plugin Authoring SDK](plugin-sdk.md) and does not copy its lifecycle or Bridge implementation into generated projects. Generated examples and their tests act as downstream compatibility fixtures for that public SDK.

Generated backend code imports only supported authoring APIs from `harness.sdk`. Backend-only code uses `define_backend_plugin`; full-stack code uses `define_bridge_backend_plugin`, typed RPC and Event descriptors, and the revision-bound channel supplied by `BridgeBackendPluginContext`.

Generated client code imports `defineClientPlugin`, `rpcMethod`, `clientEvent`, and `serverEvent` from `@deepseek-harness/browser-bridge-client`. Full-stack code uses the revision-bound operations on the `ClientPluginContext` constructed from the Browser Bridge adapter input. No generated source accepts Plugin ID or Revision as configuration, reads Manager internals, or constructs Bridge protocol frames directly.

Registrations and resources are created through SDK helpers that attach them to the active Cordis Effect. Each example includes observable setup and cleanup behavior so authors see that disable and update remove the old contribution.

Tests import helpers only from `harness.sdk.testing` or `@deepseek-harness/browser-bridge-client/testing`. Production plugin modules never depend on testing helpers.

## Build outputs

Python backend source is the declared runtime artifact and requires no generated copy. A backend template can be installed after its Python dependencies are available and `validate` succeeds.

Client and full-stack templates expose `pnpm run typecheck`, `pnpm run test`, and `pnpm run build`. The build is deterministic for identical source and locked dependencies, emits exactly `frontend/dist/client.js` as browser ESM, and does not embed a timestamp, absolute path, Plugin ID override, or generated Revision.

The checked-in lockfile pins the generated frontend dependency graph. Generated build output is disposable and excluded from source control; the Plugin Manager reads the exact built bytes when it computes a runtime Revision.

## Generation safety

The destination must not exist, including as an empty directory or symbolic link. Phase 7 provides no overwrite or force option. A parent directory must already exist and resolve to a directory.

The generator renders every file into a private temporary sibling under the resolved parent, validates the generated source structure without requiring ignored build output, and renames the complete directory to the requested destination. Failure removes only that generator-owned temporary directory. It never removes, truncates, merges with, or changes permissions on an existing destination.

Template paths are fixed relative paths and cannot be influenced by Plugin ID or version values. The generator rejects values containing path separators or values that cannot produce valid derived Python and npm tooling names.

## Determinism

The same generator version and normalized inputs produce the same relative paths and file bytes on every supported platform. Files use UTF-8, LF endings, stable ordering, fixed ordinary-file permissions, and exactly one trailing newline where the format permits it.

Generated content contains no wall-clock time, random identifier, current working directory, username, temporary path, environment-derived package registry, or network result. Relative and absolute spellings of the same destination do not alter file content.

## Failure handling

- Invalid kind, Plugin ID, version, parent, destination, or derived tooling name fails before any destination write.
- A destination race detected at final rename fails without changing the winning directory.
- Rendering or validation failure removes the temporary tree and reports the failing template file or rule.
- `validate` reports every root manifest and declared-artifact diagnostic in stable path order and never imports backend plugin code.
- Missing `frontend/dist/client.js` reports the required build command instead of generating or downloading it.
- Package manager, compiler, or plugin test failures remain ordinary tool failures and never cause the scaffolder to rewrite the project.

## Keyless verification

Each generated backend test mounts the exported PluginSpec through Phase 6 test helpers, observes its example registration, disposes it, and proves cleanup. Each generated client test mounts the exported Cordis TS plugin through browser SDK test helpers and proves setup and Effect cleanup without a browser or network.

The full-stack fixture additionally uses an in-memory revision-bound channel to prove one typed RPC call and one Event in each direction. It does not require an LLM key, a listening Host, Chromium, or Internet access.

Repository tests generate all three kinds into temporary parents, compare their trees and bytes with approved template fixtures, run validation, and execute their documented type checks, builds, and keyless tests against the workspace SDKs. A second generation with identical inputs proves byte equality, while an existing destination and a simulated render failure prove preservation of user files and absence of partial output.

## Acceptance criteria

- Both CLI entrypoints generate identical backend-only, client-only, and full-stack projects.
- Every generated root manifest passes Dynamic Plugin Manager validation and contains exactly the contribution sections required by its kind.
- Nested Python and frontend metadata cannot redefine Plugin ID, version, runtime API, or Revision.
- Generated backend code uses Phase 6 Python SDK APIs, and generated client code uses Phase 6 browser SDK APIs without low-level protocol construction.
- Backend templates pass Python static checks and keyless tests; client templates pass TypeScript checking, keyless tests, and deterministic ESM builds.
- A generated full-stack plugin builds, validates, activates in the assembled Host, exchanges typed RPC and Events, and removes both contributions on disable.
- Existing destinations, symlink destinations, invalid inputs, missing client builds, and mid-generation failures leave no partial plugin and do not modify user-owned files.
- Determinism tests prove identical paths and bytes for identical normalized inputs.

## Exclusions

Remote registries, package search, package installation, dependency installation, lockfile updating, signing, provenance, trust policy, untrusted-code isolation, publication, upgrade migration, and IDE integration are outside Phase 7. Custom template repositories and interactive prompting are also excluded; additional templates require a later specification.
