# Checkout integration research

## Evidence baseline — 2026-09-26

Source inspection, not a live Multica/Odoo execution test. No application workspace, task, runtime or project resource was changed during research. The original DOCX was read locally; no attachment or customer identifiers are redistributed here.

| Source | Inspected revision | Finding |
|---|---|---|
| Odoo Instance SDK main | `f7c3f7c9093529d6744c30745e220efb9aea8f80` | Native COPY checkout, external-file restore, command parity and direct guarded database cleanup exist. Caller-owned Git checkout adoption does not. |
| MYL-271 planning branch | `f94fea797fe1fe6fc6575bf6afb7f76d5960f45c` | Named sources, exact backup COPY, readiness and retention are planned predecessor contracts. Diff against main contains planning/skill files, not their production implementation. |
| Multica installed CLI | `0.5.2`, build `d45aba1cd` | `repo checkout` has `--ref`, `--fresh`, no destination flag or JSON option. Resource creation has an `execution-mode` option. |
| Multica source tag | `v0.5.2` / `02d3e7cb81ff75c351b47cb1738317a07711b68f` | Task-bound native checkout and project/daemon-wide local-directory constraint. |
| Multica selected source baseline | `v0.5.3` / `ff8b285497809e084915016c40c2bc5e5991ffbc` | Checkout and local-directory source contracts checked against the installed-version tag. |
| Multica main, supplemental check | `12f8f3f31111564e5e1b9aac3f7f916e8bba4039` | Same relevant ownership constraints; not the release compatibility promise. |
| multica-py current main | `d1b5f0e154c5587eca4cebd8bd2a6d39ae3d4d06` | Contract fixture targets Multica `v0.5.3`. Repositories expose list/add/remove, not checkout. Issue/run/metadata and scoped command APIs exist. |

Live issue state was MYL-271 `in_progress` and MYL-272 `backlog`. Do not infer shipping from their issue descriptions or the user's expectation that they are complete. Do not copy their implementation tasks into this change. Verify merged API signatures at implementation start.

## Native checkout traced end to end

1. [`runRepoCheckout`](https://github.com/multica-ai/multica/blob/ff8b285497809e084915016c40c2bc5e5991ffbc/server/cmd/multica/cmd_repo.go) reads the daemon endpoint and task context, requires the active task credential, and sends the current working directory plus URL/ref to the daemon. Successful stdout is **one path**, while the human summary is stderr. A missing JSON option is not a reason to scrape tables: a narrow SDK wrapper can decode this explicit path result and reject malformed output.
2. [`repoCheckoutHandler`](https://github.com/multica-ai/multica/blob/ff8b285497809e084915016c40c2bc5e5991ffbc/server/internal/daemon/health.go) authenticates the active task, checks workspace/task identity and ownership of the requested working directory, and delegates checkout to the daemon cache. It is not a public arbitrary-path registration API. The extension must not call it directly.
3. [`CreateWorktreeContext`](https://github.com/multica-ai/multica/blob/ff8b285497809e084915016c40c2bc5e5991ffbc/server/internal/daemon/repocache/cache.go) chooses a repo-name subdirectory, fetches refs, creates or reuses checkout and controls `agent/...` branch naming, collisions and isolated Git metadata. Fetch failure can preserve cached refs. Existing dirty/unpushed work can be retained instead of switching to the requested ref. Thus neither a requested branch nor a zero exit code proves the desired HEAD.
4. Native daemon execution owns task context, run directory, sidecars and cleanup. Source inspection of `execenv/execenv.go` shows different cleanup rules for managed and local directories. We do not fabricate run rows, context files, GC markers, hooks or task credentials. Git checkout success is not Odoo readiness and does not promise permanent checkout retention.

## Why the previous local-directory model is not selected

[`findLocalDirectoryAssignment`](https://github.com/multica-ai/multica/blob/ff8b285497809e084915016c40c2bc5e5991ffbc/server/internal/daemon/local_directory.go) selects a resource from the **project**, scoped to the daemon, and rejects two matches. [`CreateProjectResource`](https://github.com/multica-ai/multica/blob/ff8b285497809e084915016c40c2bc5e5991ffbc/server/internal/handler/project_resource.go) also rejects a second resource for that project/daemon.

This is valid for a deliberately shared project working directory, not one independent environment per issue. `in_place` serializes relevant coding tasks on the same path; `worktree` creates additional Multica-owned checkouts instead of using each independently provisioned Odoo environment. Issue metadata does not change that routing. Creating a Multica project per environment or repeatedly replacing the project's directory would add lifecycle and concurrency problems outside the requested scope.

| Model | Decision | Reason |
|---|---|---|
| OdCLI worktree + project `local_directory` per issue | Do not use here | Project-wide route, one directory per daemon; not an issue selector. |
| Native Multica checkout + core adoption | Selected | Native registration/branch/task handling; one checkout; one necessary generic SDK primitive. |
| Emulate Multica registries/private storage | Reject | No public atomic checkout-registration contract found; fragile and bypasses native ownership checks. |
| Replace Git invocation inside current core checkout | Reject | Path, branch and Git-common-dir semantics differ; current cleanup assumes SDK ownership. |

## Core seams and required changes

- `resources/environment/checkout.py`, `checkout_planning.py`, `checkout_artifacts.py`: capture provisioning and reuse COPY/neutralization without the worktree-create step.
- `resources/environment/cleanup.py`: currently derives artifact root from `worktree_path.parent` and captures `git worktree remove`. Those assumptions must be ownership-aware **before** admitting a borrowed checkout.
- Environment listing currently filters by Git common dir. Native Multica caches/isolated clones can have a different common dir from the configured Odoo project. Retain explicit core project identity separately; runtime/cwd resolution must use exact registered checkout identity, not a guessed common-dir match.
- `execution.py` and `command-execution` spec require all later process argv before mutation and explicitly permit a real domain phase boundary for result-dependent commands. Do not hide checkout → adoption behind a dynamic callback or continuation framework.
- Existing public environment runtime, diagnostics, stop/remove and backup APIs are the post-provisioning surface. Do not create an extension runtime manager.

## multica-py prerequisite

[`RepositoryResource`](https://github.com/maximchikAlexandr/multica-py/blob/d1b5f0e154c5587eca4cebd8bd2a6d39ae3d4d06/src/multica_py/resources/repositories.py) needs an additive typed `checkout_command(url, ref, options)` and convenience operation in that repository. It must use its existing command/transport, cwd/environment snapshot, timeout, redaction and compatibility contracts. Return an absolute checkout path; do not invent unavailable created/reused/branch facts. Prohibit `fresh` in this workflow. Tests should pin single-path stdout separately from stderr and distinguish timeout/unknown outcome from proved absence.

`TaskRun` already exposes issue/project/workspace/runtime identity and optional work-directory/branch facts. Missing absolute paths or runtime identity are **unverified**, not a license to reconstruct a host path from a display label. Match repository subdirectories to a verified task root, not by branch name alone. If necessary run/host evidence is absent in the supported API, return an explicit unsupported result before provisioning.

There is one additional concrete public-model gap: current `multica-py` `DaemonStatus` exposes only running/PID/uptime. Multica v0.5.3 [`runDaemonStatus`](https://github.com/multica-ai/multica/blob/ff8b285497809e084915016c40c2bc5e5991ffbc/server/cmd/multica/cmd_daemon.go) emits the [`HealthResponse`](https://github.com/multica-ai/multica/blob/ff8b285497809e084915016c40c2bc5e5991ffbc/server/internal/daemon/health.go) JSON with daemon ID, server URL, OS, lifecycle status and workspace runtime-ID lists. Extend the existing SDK result/decoder to preserve these few identity facts. Then match `TaskRun.runtime_id` to the selected workspace's runtime IDs from `daemon.status_command`; use `issues.get_command` and `issues.runs_command` for membership/path evidence. This identity check is needed now; CPU/RSS sampling and usage telemetry still belong to the deferred issue. This is source evidence, not proof of a live same-host run or a forwarded filesystem.

## Requirements from the DOCX allocated elsewhere

- Approved code/dump selection and access roles: deployment/operator policy and the calling skill, with exact selections passed into primitives.
- Full project passport: existing core configuration plus a minimal Multica-project link; no second version of module inventory, responsible people or scenario catalog.
- Docker topology, domains/proxy, anonymization and restrictive credentials: infrastructure/workflow, not inferred from binding.
- Analysis, transfer cards, testing strategy, MR assignment and reports: skills/scripts/Temporal activities consuming these APIs.
- One user-facing scenario: caller composes finite commands and retains their results; the libraries do not become that scenario's scheduler.
