# Design

## Context

See `proposal.md` for why. `main` already has `[tool.uv.workspace] members = ["packages/odcli-multica"]`. Translation export and `GitResource.commit_command()` / `commit_context()` are public and already capture one process snapshot. `commit_context()` requires the description it embeds, so it cannot be the model's input. Optional `msgfmt` validation lives in the translation export command. `ProjectConfig.load` reads known tables and ignores other top-level tables in `.odcli/project.toml`. #38 does not yet expose a global override. #69 has not added `packages/odcli-openspec`.

The Python Codex SDK (`openai-codex`) exposes `Codex`, `thread_start(model=..., sandbox=Sandbox.read_only, approval_mode=ApprovalMode.deny_all)`, `thread.run`, and `codex.models()`. The SDK starts the local app server. This package must not call `codex exec`.

## Goals / Non-Goals

**Goals:**

- One workspace member and one public workflow client with `*_command()` snapshots for fill and message generation.
- Core flags that import `odcli_codex` only when the flag is used.
- Fake Codex completions in unit tests.

**Non-Goals:**

- A plugin registry, dynamic Click commands, PO/POT generation, document translation, or AI-chosen commit prefixes.
- Publishing the `odcli-codex-v0.1.0` tag in this change.
- Changing `uv build` at the repo root so it still emits one core wheel.

## Decisions

1. **Member list, not a glob.** Append `packages/odcli-codex` to the existing explicit member list. A `packages/*` glob would claim #69's directory and fail if that directory is absent. Workspace source override:

   ```toml
   dependencies = ["odoo-instance-sdk>=0.1.0,<0.2", "openai-codex"]
   [tool.uv.sources]
   odoo-instance-sdk = { workspace = true }
   ```

   `uv build --package odcli-codex --no-sources --out-dir dist/odcli-codex` keeps the version range. Pin `openai-codex` to the lowest current release that provides `Codex`, `Sandbox.read_only`, `ApprovalMode.deny_all`, and `models()`, once `uv lock` resolves it.

2. **Public plans are action snapshots plus one nested validator.** `odoo_instance_sdk.execution.action_command()` builds a `Command` from `ActionStep` values and `PreparedAction` records so the extension never imports `odoo_instance_sdk.internal`. Codex turns and catalog writes are actions. Details carry paths, counts, language, model, and byte sizes. They do not carry prompt text, patch text, catalog bodies, or secrets. The callback starts each action on the ledger and completes it only after the effect succeeds.

3. **msgfmt stays in core.** Add public `catalog_validation_command(path) -> Command[...]` next to the existing translation types. It captures absolute `msgfmt --check --statistics -o /dev/null <path>` once, or an empty plan when `msgfmt` is missing. `fill_command()` closes over that command object and runs it after the temp catalog is written and before `os.replace`. A failed validator deletes the temp file and leaves the destination unchanged.

4. **PO edits are splices, not a rewrite.** Parse entries while keeping each raw block. Skip the header and any block marked fuzzy. Replace only empty `msgstr` / `msgstr[n]` lines. Compare placeholder and tag sets (`%(name)s`, `%s`/`%d`, `{name}`, `<tag>`, and `\\` escapes) between `msgid` and the reply. On mismatch, record the `msgid` and skip that entry. Parse the result again, then validate, then replace.

5. **Commit description flow.** `GitResource.staged_patch_command()` runs `git diff --cached --no-ext-diff` through the existing Git process helper, redacts stdout, and caps the returned text at 256 KiB with a `truncated` flag. The public process step does not put the patch in `input_preview`. `odcli-codex` calls that command, adds bounded `--context` files, and asks Codex for one line. The line must be non-empty ASCII, at most 100 characters, with no control characters. Core `git commit --generate` then calls the existing `commit_command(description=line)` once and passes that same object to preview or execution.

6. **Description sources on `git commit`.** The positional argument becomes optional. Exactly one of positional text, `-`, or `--generate` is required. `-` reads stdin and uses the same one-line checks. `--context` and `--model` without `--generate` are `click.UsageError`. `--repo` / `--member-id` / `--tag` behavior stays as it is; generation supplies only `description`.

7. **Export integration.** `--auto-translate` runs after `export_translations_command(...).run()` returns. It calls `odcli_codex.integration.fill_exported_catalogs(paths, ...)`. Import happens inside that call path only. A missing import raises `ConfigError` with the uv hint. Export errors still happen before fill. Fill failure does not roll back the already published export; the error says the catalogs were exported and fill did not finish.

8. **Context selection.** Resolve the project with `commands.context.resolve_project_path`. Reject paths that escape the project root after `resolve()`. List files with `git ls-files -z` from the project root so ignored and untracked files drop out. Treat a NUL in the first 8 KiB as binary. Apply the secret-like name and content checks before adding bytes. Stop with an error at `max_context_bytes` rather than truncating.

9. **Codex adapter.** One module imports `openai_codex` and exposes `complete(model, prompt) -> str` and `listed_models() -> tuple[str, ...]`. Threads are ephemeral, read-only, and `deny_all`. The client takes that pair as a constructor argument so tests never open the app server. Model check uses `listed_models()` and fails if the effective model is absent.

10. **CLI output.** The extension uses `odoo_instance_sdk.commands.output` (`emit`, `success_document`, `failure_document`). `--plain` on `git message` writes the line with a single stdout write in that same output helper if one exists; otherwise a dedicated plain branch that still goes through the core emitter's plain mode. No second serializer.

11. **CI.** Add a workflow job that syncs the workspace, runs ruff and strict mypy on `packages/odcli-codex`, runs its pytest, builds the member with `--no-sources` into `dist/odcli-codex`, builds the core wheel, and smoke-installs core-only and core-plus-extension. A unit test scans the extension sources for `odoo_instance_sdk.internal`. Root `make package` stays core-only so the existing single-wheel assertion holds. Extend that assertion to reject `openai-codex` in core metadata.

## Risks / Trade-offs

- [openai-codex model-list shape differs from the documented `models()` call] → isolate it in the adapter and fix against the locked package before opening the PR.
- [Fill failure after a successful export leaves untranslated catalogs] → the error states that export finished; we do not delete a valid export.
- [Staged patches can still contain secrets git does not know about] → redact configured secret values and refuse secret-like context files; the patch step also drops lines that match the same content markers.
- [Large PO files exceed the context budget] → the command fails with the byte count instead of splitting the request silently.
- [#69 later switches members to a glob] → an explicit member entry remains valid either way; rebase if the lockfile conflicts.

## Migration Plan

No data migration. Existing export and commit commands keep their behavior when the new flags are absent. Rollback is reverting the member and the flags. Users who installed the extension remove it by reinstalling core without `--with-executables-from odcli-codex`.

## Open Questions

None that change the specs. The exact `openai-codex` pin is whatever `uv lock` selects from the current index, recorded in `uv.lock` and the member `pyproject.toml`.
