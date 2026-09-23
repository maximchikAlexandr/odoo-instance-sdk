## ADDED Requirements

### Requirement: E/F test quality cleanup

The list of E/F-grade tests SHALL be produced first as `openspec/changes/address-alpha-testing-defects-74/ef-inventory.md` using design D17 heuristics (nodeid, grade, reason, rewritten|replaced|deleted). Each listed test SHALL then be rewritten, replaced, or deleted per that table. Deletion SHALL preserve behavioural coverage elsewhere; a "did not crash" assertion SHALL NOT be accepted as a replacement. Line and branch coverage SHALL NOT drop below the merge-base percents recorded in that file.

#### Scenario: no E/F tests remain in the changed scope

- **WHEN** the E/F test quality gate runs on the changed scope
- **THEN** no E/F-grade tests remain

#### Scenario: each E/F test has a recorded decision

- **WHEN** the cleanup record is inspected
- **THEN** each original E/F test has a rewritten/replaced/deleted decision with a reason

#### Scenario: coverage does not drop

- **WHEN** the full test suite runs after the cleanup
- **THEN** line and branch coverage are not below baseline

## MODIFIED Requirements

### Requirement: Installed metadata is the CLI version source

`odcli --version` SHALL read optional PEP 610 `direct_url.json` via `importlib.metadata`. If `vcs_info.commit_id` is a hex string of length at least 7, the human version output SHALL append the first 7 characters, e.g. `odcli, version 0.1.0 (6a984c7)`. Wheel/sdist installs without `direct_url.json` SHALL keep the package version. Malformed or missing metadata SHALL safely fall back to the package version. The command SHALL stay fast, SHALL NOT call Git, SHALL NOT require a checkout or network, and SHALL NOT import operation-only dependencies. No build-time Git dependency, separate version registry, or network check SHALL be introduced.

#### Scenario: Installed wheel reports its metadata version

- **WHEN** an isolated environment installs a built wheel and runs `odcli --version` outside a project
- **THEN** the command exits `0` and its output contains the version declared by that wheel's distribution metadata

#### Scenario: Version support adds no dependency

- **WHEN** the built wheel metadata is inspected after the change
- **THEN** its runtime dependency set is unchanged by version discovery

#### Scenario: VCS install shows commit

- **WHEN** `odcli --version` runs on a uv-tool VCS install
- **THEN** the output shows the package version and the actually installed short commit

#### Scenario: wheel install shows package version only

- **WHEN** `odcli --version` runs on a wheel/sdist install without `direct_url.json`
- **THEN** the output shows only the package version

#### Scenario: malformed metadata falls back

- **WHEN** `odcli --version` runs with malformed `direct_url.json`
- **THEN** the output shows only the package version and does not crash

### Requirement: Architecture regression gates

The architecture regression gates SHALL include: the `PUBLIC_LEAF_CASES` SDK-first contract; line-specific ban on bare `httpx` outside `internal/transport/` files; zero `xmlrpc.client.ServerProxy` under `src/`; the `self-update` lock `odcli-update.lock` and new-process migration; and the E/F inventory plus cleanup. Findings SHALL shrink; a new finding SHALL NOT be silenced by an undocumented allowlist.

#### Scenario: Direct process launch is added

- **WHEN** production code adds `subprocess.run` or `subprocess.Popen` outside `internal/proc`
- **THEN** CI fails and identifies the launch site

#### Scenario: Output boundary is bypassed

- **WHEN** production code adds `print`, `click.echo`/`secho`, direct stdout/stderr writes, or `Console().print` outside the documented boundary
- **THEN** CI fails and identifies the output site

#### Scenario: Imprecise annotation is added

- **WHEN** a production annotation contains explicit `Any` or bare `object`, including quoted or qualified forms
- **THEN** CI fails and identifies the annotation

#### Scenario: transport gate bans bare httpx

- **WHEN** the architecture gate runs on `src/`
- **THEN** direct `import httpx`, `httpx.get/request/stream`, and `httpx.Client` creation outside the line-specific `internal/transport/` allowlist are rejected

#### Scenario: src has no ServerProxy

- **WHEN** the architecture gate runs on `src/`
- **THEN** `xmlrpc.client.ServerProxy` is absent

#### Scenario: E/F test quality gate

- **WHEN** the E/F test quality gate runs after `ef-inventory.md` is applied
- **THEN** no listed E/F-grade tests remain and coverage is not below the recorded merge-base baseline

