# Python Package Layout

This reference defines the distribution and import names for the Python Harness. The naming rule applies to project-owned source, tests, documentation, and dynamically loaded backend plugin examples.

## Names

- The Python distribution name is `deepseek-harness-python`.
- The import root is `harness`.
- Project-owned Python source lives under `src/harness/`.
- Public modules use the `harness.cordis`, `harness.agent`, `harness.plugins`, and `harness.bridge` namespaces.

Distribution metadata and Python imports serve different consumers, so the distribution name does not determine the import root. Packaging discovery must continue to use the `src` layout.

## Import behavior

All project-owned imports and generated backend plugin fixtures must use `harness`. The package does not expose a `deepseek_harness` compatibility module because this pre-release project has no external compatibility promise.

An environment synchronized from this repository must resolve `import harness` to `src/harness/__init__.py`. Importing `deepseek_harness` must raise `ModuleNotFoundError`.

## Failure handling

Packaging, tests, and plugin loading fail normally when code imports an unavailable module. The runtime must not alias, redirect, or silently accept `deepseek_harness`.

## Acceptance criteria

- The built and editable distributions expose `harness` and its public subpackages.
- Production source and plugin fixtures contain no `deepseek_harness` import or `src/deepseek_harness` path.
- The unit suite, Ruff, strict Pyright, and bytecode compilation pass with the specified layout.
- A clean runtime imports `harness` and rejects `deepseek_harness`.

## Exclusions

This change does not rename the distribution, repository, plugin manifest fields, or TypeScript packages. It does not add compatibility aliases or change runtime behavior beyond Python module resolution.
