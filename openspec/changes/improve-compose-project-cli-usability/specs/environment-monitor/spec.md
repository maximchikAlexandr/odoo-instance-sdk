## MODIFIED Requirements

### Requirement: Git activity relative to default branch

`GitActivity` fields are those in Canonical snapshot types. For each environment, the collector SHALL use that environment catalogue row's non-empty recorded `base_ref` as the baseline and SHALL expose it in the existing `default_branch` compatibility field; it SHALL NOT hardcode or guess `main`, consult a manifest default, fetch, or pull. The bounded recorded collector and direct collector SHALL use the same baseline and result semantics.

The collector SHALL first resolve `<base_ref>@{upstream}` when available and otherwise the local `<base_ref>` ref, using argv Git calls with the existing bounds. It SHALL compute ahead and behind against that baseline, and `diff` SHALL be committed three-dot `git diff --numstat <merge-base>...HEAD`; uncommitted worktree or index changes SHALL not be included. Binary files SHALL contribute zero lines. An unavailable baseline or no common ancestor SHALL produce `state="orphan"` with nullable counts and diff, but a missing hardcoded `main` SHALL have no effect. The cache key SHALL include the worktree, HEAD SHA, and resolved baseline SHA with the existing TTL.

#### Scenario: Non-main baseline is clean

- **WHEN** an environment records `base_ref=dev`, has no `main`, and HEAD equals the available local or upstream `dev` tip
- **THEN** `default_branch="dev"`, `state="clean"`, `ahead=0`, `behind=0`, and `diff={added:0,deleted:0}`

#### Scenario: Diverged with line counts

- **WHEN** HEAD is four commits ahead and one behind the recorded baseline tip
- **THEN** `state="diverged"`, `ahead=4`, `behind=1`, and `diff` reports committed added and deleted text lines

#### Scenario: Orphan no common ancestor

- **WHEN** HEAD has no common ancestor with the recorded baseline
- **THEN** `state="orphan"`, `ahead is None`, `behind is None`, and `diff is None`

#### Scenario: Binary and working-tree changes are excluded

- **WHEN** the committed three-dot diff contains a binary file and the worktree contains uncommitted text changes
- **THEN** the binary contributes zero and the uncommitted changes do not affect `diff`

#### Scenario: Recorded and direct collectors agree

- **WHEN** the same environment with `base_ref=dev` is collected through recorded bounded probes and direct probes
- **THEN** both results use the same resolved baseline and produce equal Git activity values

## ADDED Requirements

### Requirement: Public snapshots do not expose local worktree paths

The canonical monitor `Snapshot`, FastAPI snapshot response, generated HTTP schema, and dashboard payload SHALL NOT add absolute environment worktree paths. CLI-only worktree-path output SHALL join the existing catalogue model by stable environment ID after the one existing snapshot collection and SHALL NOT trigger a second metrics collection.

#### Scenario: CLI path output leaves HTTP contract unchanged

- **WHEN** an environment with a stored absolute worktree path appears in both CLI inventory and the HTTP snapshot
- **THEN** the CLI may expose the catalogue path while the monitor/FastAPI/dashboard snapshot contains no local-path field

#### Scenario: One metrics collection

- **WHEN** `env list` enriches CLI output with stored catalogue paths
- **THEN** it performs no second monitor or metrics collection and joins by stable environment ID
