# Dynamic Plugin Manager Specification

Status: Phase 3 normative specification

## Purpose

The Dynamic Plugin Manager owns one logical plugin across optional Python backend and TypeScript browser contributions. It discovers and validates plugin artifacts, activates or removes their runtime contributions, publishes immutable client revisions, and reports aggregate state.

## Scope

Phase 3 includes trusted local plugin directories, root manifest parsing, content revisions, Python entrypoint loading, PyCordis Fiber ownership, client bundle publication, enable, disable, update, rollback on one activation attempt, and observable status.

Remote package download, dependency installation, signatures, registry distribution, untrusted code isolation, persistent inventory, and browser delivery are outside Phase 3. Browser delivery is supplied by Phase 4; a process-backed `BackendHost` must replace the trusted in-process host before third-party backend plugins are admitted.

## Root manifest

Every plugin directory contains `plugin.toml`. Its `[plugin]` table is the only identity and version authority. At least one of `[backend]` or `[client]` is required.

```toml
[plugin]
id = "com.example.echo"
version = "1.0.0"
runtime_api = "1"

[backend]
entrypoint = "backend/plugin.py:plugin"

[client]
bundle = "frontend/dist/client.js"
platform = "web"

[protocol]
schema = "protocol/api.schema.json"

[activation]
backend = "required"
client = "optional"
```

Contribution and protocol paths are relative, normalized, and contained by the plugin root. Symlink resolution may not escape that root. The backend entrypoint is `<relative-python-file>:<attribute>`. The attribute is a `PluginSpec` or a zero-argument factory returning one. Nested `pyproject.toml` and `package.json` files are build inputs and cannot redefine identity or version.

Activation policy is `required` or `optional` for each present contribution and forbidden for an absent contribution. Unknown fields fail validation so misspelled permissions or paths do not disappear silently.

## Revision identity

A `PluginRevision` is the SHA-256 digest of the normalized manifest bytes and every declared backend, client, and protocol artifact. The Manager reads each file once per revision build and retains immutable bytes for client and protocol publication.

Changing declared content produces a new revision even when the semantic version was not changed. Enabling the same installed revision is idempotent. Updating requires a different revision; version policy can become stricter before distribution support is added.

## Runtime records and states

The Manager stores one record per Plugin ID:

```text
DISCOVERED -> VALIDATED -> STARTING -> ACTIVE
                              |          |
                              v          v
                           FAILED     DISABLING -> DISABLED
                              |
                              v
                           DEGRADED
```

Each record exposes the manifest, source root, current revision, desired enablement, backend Fiber state, published client revision, aggregate state, and the most recent structured diagnostic. The record, not a language-specific package file, owns status.

## Discovery and installation

`discover(directory)` scans immediate child directories in stable name order and returns every valid `plugin.toml` plus diagnostics for invalid candidates. Discovery does not import Python or publish client bytes.

`install(plugin_root)` validates one artifact and creates or replaces a disabled record. A different root cannot claim an installed Plugin ID until the existing record is uninstalled. Uninstall requires the plugin to be disabled and removes its inventory and unpublished revision bytes.

## Backend activation

`BackendHost.start(revision, context)` returns a backend activation handle. The trusted in-process host loads the declared file under a revision-qualified module name, resolves its exported PluginSpec, and mounts it as a child of the Manager Context. The handle owns that Fiber and stops it through normal PyCordis disposal.

Each backend activation receives a private `PLUGIN_RUNTIME_IDENTITY` Service containing the Manager-authoritative Plugin ID and Revision. The Service uses an isolated Realm and is removed with the backend activation. Backend plugins use this identity for revision-qualified Browser Bridge RPC and Event registrations instead of deriving a digest or accepting identity through user configuration.

The in-process host guarantees resource and registration teardown, not Python code eviction. A process-backed host will preserve the Manager interface while replacing module lifetime and wire transport.

## Client publication

`ClientArtifactRegistry.publish(plugin_id, revision, bundle)` stores immutable bundle bytes addressed by Plugin ID and revision and returns a disposer. Publication does not claim that any browser loaded the revision. Phase 4 consumes this Registry to serve bundles and reconcile connected pages.

Client-only plugins are valid and become active after publication. Backend-only plugins are valid and become active after the backend Fiber reaches `ACTIVE`. Full-stack plugins require both contributions according to their activation policies.

## Enable, disable, and update

Enable builds one immutable revision, then starts backend and publishes client contributions. If a required contribution fails, every contribution created by that attempt is disposed and the record becomes `FAILED`. If only an optional contribution fails, the successful contribution remains and the record becomes `DEGRADED`.

Disable first marks the record non-serving, removes client publication, disposes the backend Fiber, waits for cleanup, and becomes `DISABLED`. Repeated disable is idempotent.

Update builds and validates the candidate before touching the active revision. It then disables the current activation and enables the candidate. Candidate failure leaves no partially active candidate and retains the previous revision metadata for an explicit rollback; it does not claim the previous code is still running.

## Concurrency

All mutations for one Manager are serialized. Read snapshots are immutable. A second enable, disable, update, or uninstall waits for the current operation and then evaluates the resulting state. Plugin code may trigger ordinary PyCordis convergence but cannot recursively mutate its own Manager record.

## Failure handling

- Invalid TOML, unknown fields, unsafe paths, missing artifacts, duplicate IDs, unsupported runtime API, and invalid entrypoints fail before activation.
- Backend import, factory, mount, or Fiber activation failures become structured diagnostics and roll back attempt-owned contributions.
- Client publication failures follow required or optional activation policy.
- Cleanup errors remain attached to the activation record and do not restore a serving state.
- A plugin in `FAILED` or `DEGRADED` can be disabled and then explicitly retried.

## Acceptance criteria

- Manifest tests cover backend-only, client-only, full-stack, unknown fields, absent contributions, invalid activation policy, and root-escape paths.
- Revision tests prove deterministic hashing and changes for backend, client, protocol, or manifest bytes.
- A backend plugin file can be installed and enabled at runtime, provide a PyCordis Service, and remove it completely on disable.
- An active backend resolves its exact Manager-owned identity while another plugin cannot observe that isolated identity Service.
- A client bundle is published by immutable revision and removed on disable without claiming browser activation.
- Full-stack required failure rolls back the other contribution; optional failure yields `DEGRADED`.
- Update loads a distinct revision-qualified backend module and never leaves both revisions serving.
- Concurrent mutation tests prove serialized, idempotent enable and disable behavior.
- `docs/progress.md` records trusted in-process loading as an explicit limitation until a process-backed host is implemented.
