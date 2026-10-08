## ADDED Requirements

### Requirement: Main-checkout automation command surface
The root `--project PATH` selector SHALL address a main checkout for `db refresh`, `db restore`, `run --detach [--wait-ready]`, runtime status/stop, module context, `publish`, and `unpublish` without creating an environment worktree. Each bounded command SHALL have one public SDK primitive, one canonical machine envelope, project provenance, stable error code, and a typed result containing the identities required by its capability. Environment-only replacement/removal semantics SHALL continue to reject project context.

#### Scenario: Prepare then run a main checkout
- **WHEN** independently invoked project restore and detached run commands succeed
- **THEN** restore returns the published database/filestore binding and run returns the exact project runtime/readiness identity that consumed it

#### Scenario: Failed project preparation
- **WHEN** project refresh or restore fails
- **THEN** the machine failure includes backup/target/configuration facts known at failure and a stable sanitized reason while the prior binding remains active

### Requirement: Adoption and publication CLI leaves
`env adopt`, `publish`, and `unpublish` SHALL be registered as canonical leaves with SDK primitives, dry-run support for mutating operations, standard confirmation in machine modes where destructive cleanup applies, and Rich/JSON/TOON parity. `publish` and `unpublish` SHALL require exactly one project or environment selector; implicit cwd resolution MAY select an exact registered adopted environment but SHALL NOT select an arbitrary directory.

#### Scenario: Publish selector conflict
- **WHEN** both `--project` and `--env` are provided or neither can be resolved exactly
- **THEN** the CLI exits with usage/context error before runtime or Caddy probes

### Requirement: Native Git and GitLab CLI routing
The `git` group SHALL reserve literal `--` for raw native Git passthrough and SHALL preserve existing named subcommands. The `gitlab mr publish` leaf SHALL accept description only through `--description-file`, expose dry-run and bounded machine output, and resolve Multica/GitLab identity without a user-ID option. Native passthrough SHALL remain a documented raw transport exception rather than a bounded CLI envelope.

#### Scenario: Existing Git subcommand remains stable
- **WHEN** a caller invokes `odcli git sync --push`
- **THEN** Click dispatches the typed sync leaf rather than raw passthrough

#### Scenario: MR description is not file-backed
- **WHEN** a caller omits `--description-file` or supplies a missing, non-regular, symlinked, oversized, or invalid UTF-8 file
- **THEN** publication fails before GitLab lookup or mutation

### Requirement: Monitor external mode is explicit
`odcli monitor` SHALL preserve loopback-only local mode by default and SHALL add an explicit publication mode that binds only to the configured local proxy-facing interface, registers the captured monitor endpoint for the panel Caddy route, and supplies exact allowed external Host and trusted proxy settings to `run_server()`/`create_app()`. It SHALL NOT expose an unauthenticated arbitrary network bind.

#### Scenario: Default monitor remains local
- **WHEN** monitor starts without external publication mode
- **THEN** it accepts only loopback bind/Host behavior and no panel route is published

#### Scenario: External monitor configuration is incomplete
- **WHEN** external mode lacks a valid panel host, trusted proxy, Basic Auth, or publication settings
- **THEN** startup fails before network bind or Caddy mutation
