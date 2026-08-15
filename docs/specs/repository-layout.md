# Repository Layout Alignment Specification

English | [中文](repository-layout.zh.md)

Status: complete

## Purpose

The repository mirrors the top-level organization of DeepSeek Harness: browser infrastructure and Python infrastructure are sibling workspaces, while shared documentation remains at the repository root.

## Required layout

```text
frontend/                 # TypeScript Cordis browser runtime and SDK
python/                   # Python Harness project
  harness/                # import harness
  tests/                  # Python tests
  pyproject.toml
  uv.lock
docs/                     # shared specifications and progress
README.md
README.zh.md
```

`frontend/` and `python/` are independent build surfaces. A plugin's optional `frontend/` directory remains nested under that plugin root; it does not change the repository-level ownership of the browser workspace.

## Python project boundary

The Python project is invoked with `uv --directory python ...` or from inside `python/`. Its import root remains `harness`; no `src/` directory or `deepseek_harness` compatibility import is introduced. Python package data, tests, build outputs, and lock metadata belong to `python/`.

Root documentation and browser commands use explicit `frontend/` and `python/` paths so neither workspace depends on the caller's current directory. The layout change does not alter plugin manifests, runtime APIs, wire protocols, or package names.

## Acceptance

- Root contains sibling `frontend/` and `python/` workspaces.
- Python tests, static checks, builds, wheel smoke, and scaffolded project checks pass from the documented root commands.
- Frontend typecheck, tests, and build pass from `frontend/`.
- Documentation references and bilingual pairing remain valid.
