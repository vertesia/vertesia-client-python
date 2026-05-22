# Agent Instructions

This repository contains the Python client for the Vertesia API. It combines a
small hand-written facade with a committed OpenAPI-generated client.

## Repository Layout

- `vertesia_client/client.py` is the hand-written high-level `Client` facade.
  Prefer making public client behavior changes here.
- `vertesia_client/__init__.py` exports the supported top-level public API.
- `vertesia_client/openapi/` is generated OpenAPI client code.
- `spec/vertesia-openapi.json` and `spec/vertesia-openapi.yaml` are the tracked
  OpenAPI contract used by generation.
- `scripts/regenerate.sh` runs OpenAPI Generator, patches forward-compatible
  enum behavior, and runs the unit tests.
- `test/` contains `unittest` tests. `test/test_live.py` contains opt-in live
  integration tests.
- `.github/workflows/` contains CI, release, generated-code guard, and zizmor
  workflow security checks. CodeQL uses GitHub default setup, so do not add a
  checked-in CodeQL workflow unless default setup is intentionally disabled.

## What You May Modify

Normal hand-written changes may touch:

- `vertesia_client/client.py`
- `vertesia_client/__init__.py`
- `test/`
- `README.md`, `AGENTS.md`, `CLAUDE.md`, and other docs
- `pyproject.toml` for package metadata, dependencies, tool settings, or release
  version updates
- `openapi-generator-config.yaml` when changing generator configuration
- `scripts/`, especially generation compatibility patches
- `.github/` workflow and repository automation files

When changing public behavior, update or add tests in `test/` and keep the
README examples accurate.

## What You Must Not Modify Manually

Do not manually edit or commit files under:

- `vertesia_client/openapi/`
- `spec/`

Those files are owned by internal generation automation. CI rejects pull
requests that change either directory. If generated output is wrong, change the
OpenAPI source upstream, generator configuration, or post-generation patching
logic, then let automation regenerate the client.

Do not commit local secrets or credentials:

- `.env` is for local development only.
- Live test credentials such as `VERTESIA_API_KEY` must never appear in tracked
  files, logs, fixtures, or examples.

## Generation Rules

The committed generated client exists so consumers can install releases without
running OpenAPI Generator.

Regeneration is not part of normal feature work. If regeneration is explicitly
needed, use:

```sh
scripts/regenerate.sh
```

This requires `openapi-generator` on `PATH`. The script also runs
`scripts/patch_forward_compat_enums.py` and:

```sh
python3 -m unittest discover -s test
```

Generated changes should be staged only by the dedicated generation automation,
using the expected generated paths and package metadata:

```sh
git add vertesia_client/openapi spec pyproject.toml .openapi-generator
```

## Testing And Checks

Use `python3` locally unless the environment provides `python`.

For a normal local check:

```sh
python3 -m pip install -e ".[dev]"
python3 -m ruff check .
python3 -m ruff format --check .
python3 -m unittest discover -s test
```

The package also has a `pytest` config, but CI runs `unittest` directly. Prefer
`python3 -m unittest discover -s test` for required verification.

Live integration tests are skipped unless both conditions are true:

- `VERTESIA_LIVE_TESTS=1`
- `VERTESIA_API_KEY` is set to a non-placeholder `sk-` secret key

Live tests can create and delete Vertesia resources. Do not enable them unless
the user explicitly asks and provides an appropriate environment.

## Python Style

- Support Python `>=3.9`.
- Keep line length at 120 characters.
- Ruff checks use `E`, `F`, `I`, `UP`, and `B`.
- `vertesia_client/openapi/` is excluded from Ruff because it is generated.
- Prefer the standard library for facade logic unless a dependency is already
  part of the package contract.
- Keep imports and typing compatible with Python 3.9. This repo already uses
  `from __future__ import annotations` where newer typing syntax is needed.

## Client Design Notes

- `Client` is the recommended user entry point.
- The facade routes Studio and Store APIs through generated API groups.
- `api_key` performs STS token exchange and requires an `sk-` secret key.
- `token` uses an existing bearer token and bypasses STS.
- `api_key` and `token` are mutually exclusive.
- Custom split endpoints using `api_key` require `token_server_url` unless STS
  can be safely derived from a Vertesia `api*` host.
- The default `x-api-version` header is part of the client contract; update it
  deliberately and test header behavior.
- Generated models are patched for forward compatibility: unknown response
  fields are ignored, unknown standalone enum values are preserved, and inline
  enum validators should not reject raw values of the correct JSON type.

## Release And Version Rules

For release version changes, keep these values in sync:

- `pyproject.toml` project version
- `openapi-generator-config.yaml` `packageVersion`
- `spec/vertesia-openapi.json` `info.version`

The release workflow verifies these values before publishing. Since `spec/` is
generated-owned, coordinate version changes with the generation automation
instead of editing spec files manually in a normal PR.

## GitHub Actions Security

Workflows are audited by `zizmor`. CodeQL is handled by GitHub default setup for
this repository; avoid adding a checked-in advanced CodeQL workflow while
default setup remains enabled.

- Pin third-party GitHub Actions by full commit SHA.
- Keep a trailing comment with the exact corresponding tag, for example
  `# v4.35.5`.
- For annotated tags, pin the peeled commit SHA, not the tag object SHA.
- Keep `permissions` minimal and prefer `permissions: {}` at workflow scope.
- Use `persist-credentials: false` for checkout unless a workflow explicitly
  needs push credentials.
- After workflow changes, run:

```sh
uvx zizmor@1.24.1 --no-exit-codes --persona=auditor .github
```

## Dependency Updates

Runtime dependencies live in `pyproject.toml`. There is no committed Python lock
file. Keep dependency bounds compatible with Python 3.9 and avoid broad major
version changes unless the user asks for that risk.

Dependabot also manages GitHub Actions updates. When updating workflow action
pins, resolve and pin the actual commit for the desired tag and update the tag
comment at the same time.

## Agent Workflow

- Check `git status --short --branch` before editing.
- Keep generated-code changes out of normal commits.
- Preserve unrelated user changes in the worktree.
- Prefer small, focused commits and describe verification performed.
- If you cannot run a relevant check because a tool or dependency is missing,
  state that clearly.
