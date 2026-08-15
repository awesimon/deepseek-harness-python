# AGENTS.md

This repository is the Python rewrite of DeepSeek Harness. Its `frontend/` and `python/` workspaces are siblings, matching the top-level organization of the TypeScript project.

## Source-reading discipline

Read `docs/source-notes/deepseek-harness-ts.md` before consulting the TypeScript implementation. Treat that note as the local map of established behavior.

Only reopen TypeScript source when the note does not answer a concrete semantic question. After resolving the question, update the note in the same change with:

- the behavior that matters to the Python implementation;
- the authoritative TypeScript file or document;
- any compatibility decision made for Python.

Do not repeatedly scan the original repository to regain context already captured in the note.

## Architecture rules

- Browser plugins run in the existing TypeScript Cordis runtime.
- Backend plugins run in PyCordis.
- A logical plugin may contain a backend contribution, a client contribution, or both.
- Cross-runtime communication uses explicit versioned wire contracts. Runtime objects, contexts, fibers, and disposers never cross the wire.
- Model-visible input is durable before it reaches an LLM request.
- Registrations belong to effects and disappear when their owning fiber unloads.
- Python APIs should preserve Cordis semantics without imitating JavaScript Proxy, Symbol, or declaration-merging implementation details.

## Planning and progress

- Every implementation phase starts with a normative document under `docs/specs/` that defines scope, behavior, failure handling, acceptance criteria, and explicit exclusions.
- Update `docs/progress.md` in the same change as implementation work. Record the current state, completed evidence, remaining work, and the next concrete milestone.
- A phase is complete only when its acceptance criteria have executable evidence. Do not infer completion from code presence alone.

## Commands

```sh
uv --directory python run python -m unittest discover -s tests
```

Use the standard library in the Cordis kernel unless a maintained dependency removes substantial owned lifecycle or validation code.
