## ADDED Requirements

### Requirement: Public typed project removal operation

`OdooClient` SHALL expose a `projects` resource with a typed `remove_command()` returning one immutable `Command[ProjectRemovalResult]` and a convenience `remove()` that delegates to that captured command. The public selector SHALL contain exactly one canonical project path or full `project_id`. Public plan, decision, result, and error-detail values SHALL be frozen, strictly typed, JSON-safe, lazily exported, and free of Click, Rich, private executor, and secret values.

#### Scenario: Path selector resolves canonical registration

- **WHEN** removal receives the registered project root as its path selector
- **THEN** it resolves the exact catalogue `project_id` from canonical repository provenance and captures one immutable plan

#### Scenario: Missing source resolves by full id

- **WHEN** the registered source directory no longer exists and removal receives its full `project_id`
- **THEN** it resolves the durable catalogue row without filesystem discovery and remains eligible for forget or retry

#### Scenario: Invalid selector is inert

- **WHEN** both selector forms, neither form, an abbreviated id, or an unknown id is supplied
- **THEN** removal fails before command construction, lock acquisition, prompt, process, filesystem, database, Docker, or catalogue mutation

### Requirement: Immutable complete removal projection

The captured plan SHALL contain every known project-owned or related local resource exactly once with stable identity, dependency order, sanitized target, ownership confidence, active-use state, and one explicit action/reason. Actions SHALL be limited to `stop`, `unpublish`, `delete`, `retain`, and terminal `forget`. Planning SHALL reuse the existing catalogue and local resource collectors and SHALL NOT mutate, reconcile, adopt, create a second catalogue, or fabricate ownership.

#### Scenario: Mixed ownership plan

- **WHEN** a project has SDK-owned and caller-owned worktrees, owned and borrowed backups, an owned Compose cluster, and an external shared resource
- **THEN** the plan marks only proven exclusive SDK resources for mutation and lists every other resource as retained with a stable reason

#### Scenario: Dry-run and execution share a snapshot

- **WHEN** the same captured command is inspected and then executed
- **THEN** execution consumes its frozen ordered steps without rebuilding paths, argv, resource decisions, or confirmation text

#### Scenario: Secrets stay absent

- **WHEN** the plan includes project configuration, dotenv, database, publication, and process resources
- **THEN** public projections contain no secret value, config body, process environment, database password, or unsanitized diagnostic

### Requirement: Forget preserves data and ownership

Without purge, project removal SHALL mutate only registration state after a locked safety preflight. Source, manifest, secrets, environments, Python environments, databases, filestores, backups, logs, temporary files, containers, volumes, and remote resources SHALL remain unchanged, and their existing project ownership SHALL remain queryable internally by full `project_id`.

Forget SHALL refuse while a non-removed environment, live or unresolved-stale runtime, active publication, cleanup-in-progress record, or other active managed resource would be hidden from ordinary catalogue/monitor discovery. Inert retained resources SHALL NOT by themselves block forget.

#### Scenario: Safe forget

- **WHEN** a registered project has no active managed resource and remove runs without purge
- **THEN** the project becomes forgotten, disappears from ordinary catalogue and monitor views, and every file, secret, database, backup, container, volume, and ownership relation is byte-for-byte or row-for-row unchanged

#### Scenario: Active resource blocks forget

- **WHEN** the project has a ready environment, live runtime, active publication, or cleanup-in-progress resource
- **THEN** forget fails with the blocking stable identities and changes neither registration nor resource state

#### Scenario: Repeated forget is idempotent

- **WHEN** forget is repeated for an already forgotten project by full id
- **THEN** it succeeds with the same forgotten state without changing ownership or `forgotten_at`

### Requirement: Exact purge confirmation

Purge execution SHALL require the exact token `<registered-root-basename>:<full-project_id>` derived from the captured catalogue row. A Rich TTY caller SHALL enter that token interactively or pass it through `confirmation`; a non-interactive or machine caller SHALL pass it explicitly. Missing, shortened, malformed, or mismatched confirmation SHALL fail before all mutation. A generic yes value SHALL NOT satisfy the token. Dry-run SHALL show the token and plan without prompting or treating any token as execution authority.

#### Scenario: Exact interactive confirmation

- **WHEN** a Rich TTY caller enters the exact displayed project-name and full-id token
- **THEN** the captured purge command may proceed to locked revalidation

#### Scenario: Generic or shortened confirmation is refused

- **WHEN** purge receives `yes`, only the project name, or an abbreviated project id
- **THEN** it reports confirmation mismatch and consumes no mutating step

#### Scenario: Non-interactive confirmation is mandatory

- **WHEN** purge executes without a TTY or through a machine output mode without an explicit exact token
- **THEN** it fails with `confirmation_required`, emits no prompt, and performs no mutation

#### Scenario: Dry-run is inert

- **WHEN** purge dry-run is requested with or without a token
- **THEN** it emits the same delete/retain plan and expected token without validating authority, prompting, or mutating any resource

### Requirement: Recoverable ownership-safe purge

Purge SHALL execute in this order: stop exact owned runtimes; unpublish exact routes; remove environments through their existing captured cleanup commands; delete provenance-authorized databases and contained filestores; delete exclusively owned unreferenced backups; destroy the exact unused SDK-owned Compose container and volume; delete contained SDK-owned global/project artifacts and an ownership-proven primary source root; then mark the project forgotten. Each child operation SHALL retain its existing lock, revalidation, confirmation-independent ownership, idempotence, and postcondition checks.

Remote Odoo instances, remote Git repositories, Docker images, external/shared clusters and databases, borrowed backups, shared Python environments, symlink escapes, unknown files, and caller/other-tool-managed checkouts SHALL be retained and reported. Purge SHALL NOT mark the project forgotten until every required mutation has completed and every retained item has a terminal reason.

#### Scenario: Complete isolated purge

- **WHEN** an exactly confirmed project contains only proven exclusive SDK resources
- **THEN** purge stops and unpublishes it, deletes those resources in dependency order, verifies their postconditions, and finally marks the project forgotten

#### Scenario: Another project and shared resources are unchanged

- **WHEN** the selected project relates to a second project or shared/external resources
- **THEN** purge retains those resources with reasons and the second project's catalogue rows, files, databases, processes, routes, containers, and volumes remain unchanged

#### Scenario: Other-tool checkout is retained

- **WHEN** the registered root or an environment checkout is caller-owned, linked, externally managed, or has ambiguous ownership
- **THEN** purge does not remove it and reports the ownership reason

#### Scenario: Unproven database or volume is retained

- **WHEN** database lifecycle evidence, active cluster claim, exclusive-use proof, Docker labels, or contained filestore identity is absent or changed
- **THEN** the affected database, filestore, container, and volume are not mutated and the decision is reported

### Requirement: Locked revalidation and concurrent safety

Initialization, forget, and purge SHALL serialize on one project lifecycle lock keyed by canonical project identity. Under that lock purge SHALL re-read registration identity/state and the resource-decision fingerprint before mutation. Child operations SHALL acquire only their existing narrower locks in stable order and SHALL revalidate their captured ownership immediately before effects.

#### Scenario: Resource set changes after preview

- **WHEN** a new environment, backup reference, publication, database event, runtime, or cluster claim appears after plan capture
- **THEN** execution refuses before the affected mutation and reports completed and remaining decisions

#### Scenario: Concurrent init cannot race final forget

- **WHEN** initialization and project removal target the same canonical identity
- **THEN** one holds the project lifecycle lock and the other observes the resulting state before it can write registration or resources

### Requirement: Partial failure and retry remain truthful

On failure, the project SHALL remain registered and the sanitized typed error SHALL identify the failed step plus ordered completed, retained, and remaining decisions. The executor SHALL never report full success after a partial failure. A later invocation by full id SHALL rebuild from durable current state, treat verified completed point operations idempotently, and continue only the remaining safe work.

#### Scenario: Failure before terminal forget

- **WHEN** a purge step fails after earlier steps succeeded
- **THEN** the error reports those completed steps and the remaining suffix, the project remains registered, and evidence required for safe retry is retained

#### Scenario: Retry after missing source

- **WHEN** source deletion succeeded but a later catalogue action failed
- **THEN** retry by full project id resolves the row, does not repeat unsafe deletion, and can complete the remaining verified actions

#### Scenario: Repeated successful purge

- **WHEN** purge is repeated by full id after successful completion
- **THEN** it returns the same forgotten terminal state with already-absent resources represented idempotently and no unrelated mutation

### Requirement: Bounded typed removal result

Successful and failed project removal output SHALL use the existing bounded redacted output pipeline. The result SHALL include full project identity, requested mode, final registration state, deterministic action totals, and bounded completed/retained/remaining decisions. Truncation SHALL preserve totals and the failed stable identity. Human and machine renderers SHALL derive from the same typed value.

#### Scenario: Machine partial failure

- **WHEN** purge fails in JSON or TOON mode after consuming actions
- **THEN** one failure envelope contains sanitized JSON-safe structured details with equal completed, retained, and remaining semantics and exits non-zero

#### Scenario: Bounded large plan

- **WHEN** a project has more resource decisions than the output bound
- **THEN** the result truncates deterministically, preserves total counts and failure identity, and emits neither secrets nor unbounded paths/logs
