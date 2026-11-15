# Tasks

## 1. Research and package scaffold

- [x] 1.1 Commit `packages/odcli-openspec/docs/research/openspec-integration.md` from OpenSpec 1.13.2 JSON output and verify the documented `list` and `status` fields match that CLI
- [x] 1.2 Add the `odcli-openspec` workspace member, public core version range, console script, and facts entry point, and verify `uv build --package odcli-openspec --no-sources` succeeds

## 2. Command continuation and checkout flag

- [x] 2.1 Add `continue_command` and verify a unit test runs the original command once and then the appended steps on the same snapshot
- [x] 2.2 Add `--openspec-change` and verify a missing `odcli-openspec` import fails before checkout planning with the `uv tool install --with-executables-from` hint

## 3. Binding, progress, and SDK

- [x] 3.1 Implement bind, unbind, and checkout composition against a fake `openspec` executable and verify partial `binding_failed` keeps the created environment
- [x] 3.2 Implement the environment-facts provider and typed progress model and verify planning counts stay distinct from task counts, including `no_tasks` and null percentage
- [x] 3.3 Preserve integer and null fact details in JSON and TOON and verify that unit test

## 4. Isolated distribution check

- [x] 4.1 Add the GitHub job that builds the core and extension wheels separately and verifies both console scripts, and leave that job for GitHub rather than a local full CI run
