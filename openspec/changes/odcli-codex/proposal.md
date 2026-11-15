# Proposal

## Why

Core `odcli` should stay installable without an AI runtime, while developers still need help filling missing Odoo translations and writing the English sentence that `odcli git commit` already formats. GitHub #68 adds that help as a separate `odcli-codex` distribution that calls the public SDK and the official Python Codex SDK.

## What Changes

- Add workspace member `packages/odcli-codex`: its own distribution, import package `odcli_codex`, console script `odcli-codex`, version `0.1.0`, and core dependency range `odoo-instance-sdk>=0.1.0,<0.2`. Published metadata stays valid with `uv build --package odcli-codex --no-sources`. The root member list gains this package only; `packages/odcli-openspec` stays owned by #69.
- Add `odcli-codex translations fill` and `CodexOdooClient.translations.fill_command()` / `fill()`. Fill only empty `msgstr` values in an existing `.po` file. Preserve headers, comments, fuzzy entries, ordering, plurals, placeholders, and markup. Reject a model reply that changes that structure for the affected entry and leave that entry untranslated. Write the catalog atomically only after it parses and optional `msgfmt` validation passes. Report the existing #54 validator output plus translated, fuzzy, and untranslated counts.
- Add optional `odcli translations export --auto-translate`. Export stays the current command. Filling runs only after a successful export, and only through `odcli-codex`. Without the package, the flag fails with the `uv tool install --with-executables-from odcli-codex odoo-instance-sdk` hint. Export without the flag is unchanged.
- Add `odcli-codex git message` and `client.git.message()`. It returns one validated English description line. Core still chooses the prefix, module, ticket, and final `git commit` argv.
- Add `odcli git commit --generate` and description `-` for stdin. Exactly one description source is allowed: positional text, `-`, or `--generate`. `--context` and `--model` are valid only with `--generate` or `--auto-translate`. Both generated flows pass the description into the existing `commit_command()` snapshot.
- Read `[codex]` from the existing `.odcli/project.toml`. CLI `--model` overrides it. There is no hardcoded model name. The installed Codex runtime must list the effective model before a turn starts.
- Tests use an injected Codex completion. CI does not call a live model or read a developer Codex login.

Assumptions recorded because #69 and #38 are still open and `commit_context()` requires the description it will embed:

- The workspace scaffold already on `main` lists `packages/odcli-multica`. This change appends `packages/odcli-codex` and does not switch the member list to `packages/*`.
- #38 has no global config override yet. Settings come from `[codex]` in `.odcli/project.toml`, then CLI flags. `ProjectConfig.load` already ignores unknown top-level tables, so core does not grow a second manifest parser for this table.
- A description is one ASCII line of at most 100 characters with no control characters. `commit_context()` remains the core check before commit planning.
- Model input for a commit description is a new read-only `GitResource.staged_patch_command()` (bounded, redacted patch text). Generation then passes only the validated description to the existing `commit_command()`.
- Context over `max_context_bytes` (default 262144) fails the command. Secret-like names (`.env`, `credentials*`, `*.pem`, `*.key`, `id_rsa`, `*.p12`) and content markers (`BEGIN PRIVATE KEY`, `AKIA`, `api_key=`, `token=`) are rejected and not sent.
- Codex runs read-only with approvals denied. This package applies accepted strings itself. It does not shell out to `codex exec` and does not store API keys.
- This pull request does not publish a tag. Metadata and CI prove the package-qualified tag shape `odcli-codex-v0.1.0`.

## Capabilities

### New Capabilities

- `odcli-codex`: optional Codex distribution for missing-PO fill and one-line commit descriptions, including install isolation, context bounds, and the public workflow client.

### Modified Capabilities

- `translation-export`: after a successful export, optional `--auto-translate` fills only the catalogs that export just wrote.
- `odoo-git-workflow`: a generated or piped description is an input to the existing immutable commit command, and staged patch text is a read-only public snapshot.
- `cli-odcli`: `translations export` and `git commit` keep their current leaves and gain the optional flags above. `odcli-codex` is a separate executable, not a new `odcli` leaf.
- `packaging`: the core wheel and a core-only install still exclude extension code and `openai-codex`.

## Impact

- New tree `packages/odcli-codex` and a root workspace member entry. `uv.lock` gains the extension and `openai-codex`.
- Core CLI flags on `translations export` and `git commit`, plus public `GitResource.staged_patch_command()` and a public catalog-validation command the extension calls without importing `odoo_instance_sdk.internal`.
- Core runtime dependencies stay the current set. `openai-codex` is an extension dependency only.
- GitHub Actions gains a package-local ruff/mypy/test job and a clean-wheel smoke job for core-only versus core-plus-`odcli-codex`.
