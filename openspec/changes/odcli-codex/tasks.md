# Tasks

## 1. Workspace member and core helpers

- [x] 1.1 Add `packages/odcli-codex` to the uv workspace, declare `odoo-instance-sdk>=0.1.0,<0.2` plus `openai-codex`, and refresh `uv.lock`. Verify `uv lock` succeeds and the root `pyproject.toml` runtime dependencies are unchanged.
- [x] 1.2 Add `action_command()` on the public execution module and `catalog_validation_command()` for optional `msgfmt`. Verify a unit test shows a missing `msgfmt` omits the process step and a present `msgfmt` captures one argv snapshot.
- [x] 1.3 Add `GitResource.staged_patch_command()` with redaction and a 256 KiB cap. Verify a unit test shows the index is unchanged, the plan omits patch text, and a secret value is redacted.

## 2. Codex package workflows

- [x] 2.1 Implement PO splicing, context selection, and `[codex]` settings. Verify package tests cover empty-versus-fuzzy entries, placeholder rejection, outside-project paths, and secret-like or oversized context.
- [x] 2.2 Implement `CodexOdooClient.translations.fill_command()` / `fill()` and `git.message()` with an injected completion and a read-only Codex adapter. Verify dry-run tests do not call the completion, plans omit prompt and patch bodies, and unsafe description lines are rejected.
- [x] 2.3 Add the `odcli-codex` Click commands, Rich/JSON/TOON via core output, and `--plain`. Verify CLI tests for help, plain stdout, and a dry-run that does not write the catalog. Document install forms in `packages/odcli-codex/README.md`.

## 3. Core CLI integration

- [x] 3.1 Add `translations export --auto-translate` with `--context` and `--model`, importing the extension only when the flag is set. Verify tests for unchanged export, the install hint, and fill-after-export using a fake filler.
- [x] 3.2 Add `git commit` description sources `-` and `--generate`, and reject mixed sources or stray `--context` / `--model`. Verify tests that generated and piped descriptions produce the same `commit_command` message and that a missing extension does not run `git commit`.

## 4. Isolation checks

- [x] 4.1 Assert the core wheel metadata has no `openai-codex`, and scan `packages/odcli-codex` for `odoo_instance_sdk.internal` imports. Verify those tests pass.
- [x] 4.2 Add a GitHub Actions job that runs package ruff, strict mypy, package tests, `--no-sources` build, and core-only versus core-plus-extension help smokes. Verify the workflow file contains those steps.
