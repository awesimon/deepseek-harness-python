# DeepSeek Harness Python

English | [中文](README.zh.md)

Python backend runtime for DeepSeek Harness. The browser keeps the TypeScript Cordis plugin runtime; this project provides PyCordis and the backend agent runtime connected through an explicit wire protocol.

The implementation starts with the lifecycle kernel because every later capability depends on its service, effect, event, and isolation semantics.

## Status

Phase 1 is complete:

- architecture and migration specification;
- TypeScript source-mechanism index;
- PyCordis service registry and fiber lifecycle;
- reversible effects;
- typed event keys and waterfall dispatch;
- service isolation realms.

Later phases add the session log, LLM and tool services, the agent loop, the browser bridge, and installable multi-face plugins. See [implementation progress](docs/progress.md) for current evidence and the next milestone.

## Layout

```text
docs/specs/          Normative design and phased implementation plan
docs/source-notes/   Stable summaries of the TypeScript reference behavior
src/deepseek_harness Python packages
tests/               Standard-library unit tests
```

## Test

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
```
