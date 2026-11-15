# Design

## Context

See proposal.md for why this change exists. Public mutations already capture one `Command` whose plan is a frozen sequence of `ActionStep` and `ProcessStep` values, executed only through `internal/proc`. Project settings live in `.odcli/project.toml`. New user files belong under the canonical `~/.odcli` root (`ODCLI_USER_ROOT` included). Odoo 19 `scaffold --template` accepts a directory, walks it, renders Jinja for a fixed set of suffixes, and supplies only `name` to a custom template.

## Goals / Non-Goals

**Goals:**

- One transport-free merge and template composition used by the CLI and SDK.
- One captured plan for preview and execution.
- Native Odoo 19 rendering, with cleanup on every outcome.

**Non-Goals:**

- A second renderer, a config editor, remote templates, or generated business models.
- Reintroducing platformdirs for new files.

## Decisions

1. **User config path is `get_config_root() / "config.toml"`.** The issue text suggested platformdirs. This repository migrated off those roots and forbids new artifacts there. `odcli config path` prints this canonical path. Alternative: follow the issue literally and split user config from `~/.odcli`. Rejected because storage migration explicitly keeps new files off the legacy roots.

2. **Project `odoo_bin` wins over `OdooClient.config.executable`.** Scaffold is a project operation. The client executable remains the fallback when the manifest has no `odoo_bin`. The chosen path must exist before a plan is returned.

3. **Bake every manifest value except the technical name.** Odoo passes only `name` into a custom template. Display name, author, license, depends, and derived `data`/`demo` lists are written into the temporary `__manifest__.py` before launch. Fragments that need the technical name use `{{ name }}` so the native renderer still performs that substitution. Values containing Jinja delimiters are rejected so user config cannot inject template code.

4. **Optional components default to off.** Baseline files plus `models`, `security`, and `views` are the built-in addon. User or project config opts into wizard, controllers, data, demo, report, tests, and `static/description`. `data` and `demo` manifest keys are derived from the selected fragments and are reserved.

5. **Plan the temporary directory without creating it.** The path is `tempfile.gettempdir()/odcli-scaffold-<sha256>/template`. The digest covers the technical name, destination, and rendered template files, so identical previews stay stable and the digest contains no secret material. Execution creates that directory, runs the captured process, then deletes the parent. On a non-zero native exit, the new module directory is removed before the error is raised. Success skips the rollback step so the ledger still accounts for it.

6. **Normalize names with Odoo's snake-case rules, then require `^[a-z][a-z0-9_]{0,63}$`.** That keeps the directory Odoo creates equal to the name we validated, and rejects `../` leftovers that snake-case does not remove.

7. **CLI stays a parser.** `commands/scaffold.py` resolves the project, parses repeatable options, and calls `ScaffoldResource.create_command()`. Output goes through `run_or_preview`.

## Risks / Trade-offs

- [Odoo overwrites files inside an existing module directory] → Refuse when the target path exists before any process starts.
- [A native failure can leave a partial tree] → Rollback deletes only the target that did not exist at planning time, then deletes the temporary template.
- [Jinja treats `{{` inside a `.py` manifest as code] → Reject delimiter-bearing manifest strings and keep authored fragments free of user text except `{{ name }}`.
- [Real Odoo is unavailable in unit CI] → Unit tests cover merge, manifest text, path safety, dry-run, and a fake executable. One `real_odoo` test runs pinned Odoo 19 `scaffold --template` on GitHub.

## Migration Plan

No stored data moves. Existing project manifests gain an optional `[scaffold]` table. Absence preserves current load behavior. Rollback is reverting the change; no catalog migration is involved.

## Open Questions

None. The platformdirs versus `~/.odcli` choice is settled by the existing storage boundary.
