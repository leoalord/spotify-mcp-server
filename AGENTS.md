# Spotify MCP Server Agent Guide

## Purpose

Build the product described in the [Spotify MCP Server PRD](https://app.notion.com/p/Spotify-MCP-Server-PRD-3b72bade7f3181d38201dc480d7c5d3c) with the smallest maintainable implementation that satisfies the current task.

Optimize for clarity, correctness, and a small dependency surface. Do not optimize for hypothetical future requirements.

## Work shape

- Implement one coherent vertical slice at a time, following the PRD build order unless the user explicitly changes priority.
- Before editing, identify the acceptance behavior and the smallest set of files that must change.
- Do not create placeholder modules, empty directories, generic frameworks, plugin systems, compatibility layers, or configuration options for work that is not part of the current slice.
- Do not mix opportunistic refactors with feature work. Refactor only when required for the requested behavior or when it removes duplication already present in the changed code.
- Introduce an abstraction only when it represents a real boundary or simplifies multiple existing call sites. Prefer a direct typed function otherwise.
- Keep MCP handlers thin: validate and route at the boundary, place reusable Spotify behavior in the shared client layer, and return explicit structured results.
- Comments and docstrings should explain constraints or non-obvious decisions, not restate the code.

## Intended boundaries

Create these areas only when the corresponding behavior is implemented:

- `server.py`: server construction, registration, and transport startup only.
- `spotify/`: OAuth, token storage, HTTP transport, retry behavior, pagination, and Spotify response handling.
- `tools/`: MCP-facing schemas and thin orchestration for the nine consolidated tools.
- `prompts/`, `resources/`, and `apps/`: prompt, resource, and UI-extension code once each phase begins.
- `tests/`: behavior-focused tests; mirror production directories only when that makes tests easier to find.

Do not add repository/service/domain layers unless concrete behavior demonstrates that boundary is needed.

## Product invariants

- Target MCP `2026-07-28` with the official Python `mcp` v2 SDK.
- Serve Streamable HTTP on loopback only for v1. Do not expose the server beyond localhost without explicit approval and MCP-layer OAuth hardening.
- Target only Spotify's supported post-February 2026 API surface. Do not restore removed batch, browse, recommendation, audio-feature, or other-user endpoints.
- Keep the public v1 catalog to the nine explicitly approved tools. Adding a tool or changing a tool schema requires explicit approval and corresponding documentation and tests.
- Do not use deprecated MCP Roots, Sampling, server-initiated protocol Logging, DCR, or legacy HTTP+SSE.
- Keep Spotify OAuth tokens and MCP-layer credentials separate. Never log secrets, persist access tokens, or commit credentials.
- All Apps-enabled behavior must retain a useful structured text/JSON fallback.
- Use Python logging to stderr. Do not write application output to stdout while serving MCP traffic.

## Dependencies and environment

- Use `uv` for environments, dependency changes, locking, and command execution. Do not introduce `requirements.txt`, Poetry, Pipenv, or ad hoc `pip install` instructions.
- Add a runtime dependency only when the standard library and existing dependencies cannot reasonably provide the required behavior. Ask before adding one.
- Add development dependencies only for an immediate, configured check used by this repository.
- Keep `pyproject.toml` and `uv.lock` in sync, and commit the lockfile once generated.
- Centralize configuration reads. Do not scatter environment-variable access throughout tool implementations.

## Testing and verification

- Add or update focused tests for every behavior change and regression fix.
- Unit tests must not require a live Spotify account or make real network calls. Mock at the Spotify HTTP boundary.
- Cover success, normalized errors, pagination boundaries, rate limiting, and token refresh where relevant to the slice.
- Do not weaken or delete a test merely to make a change pass.
- Run the narrowest relevant test during iteration, then run before handoff:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

- If a check cannot run, report the exact reason and what remains unverified.

## Commit discipline

- Each commit must represent one coherent, reviewable slice with a clear acceptance behavior.
- Run the complete verification commands above after the final edit and before creating the commit.
- Do not create a normal commit with failing or skipped required checks. If an external blocker prevents verification, stop before committing and report it.
- Review the staged diff for unrelated changes, generated clutter, credentials, and dead code before committing.
- Write commit messages that describe the behavior or constraint introduced, not the editing activity.

## Branch and merge workflow

- Treat `main` as protected. Never commit directly to it.
- Start every change from an up-to-date `main`, then create a short-lived, focused feature or fix branch before editing. Codex-authored branches use the `codex/<short-description>` prefix.
- Keep each branch limited to one coherent slice. Do not combine unrelated features, fixes, or refactors.
- Run the complete verification gate and review the staged diff before committing on the branch.
- Merge into `main` only through a reviewed pull request after all required checks pass. Never bypass branch protection, force-push `main`, or merge a failing change.
- Delete the branch after it is merged.
- If work was started on `main` accidentally, create a branch before the first commit. Do not add another direct commit to `main`.

## Approval gates

Ask before:

- adding a production dependency;
- changing the eight-tool catalog, a public tool schema, or documented semantics;
- widening Spotify OAuth scopes or changing credential storage;
- exposing the service beyond loopback;
- adding compatibility for an older or removed MCP/Spotify surface;
- materially expanding a task beyond its requested PRD slice.

Routine in-scope edits, tests, formatting, and read-only inspection do not require confirmation.

## Definition of done

A change is complete when it implements the requested behavior, contains no dead or speculative code, passes the relevant checks, preserves the product invariants above, updates user-facing documentation when behavior changes, and has been reviewed as a focused diff.
