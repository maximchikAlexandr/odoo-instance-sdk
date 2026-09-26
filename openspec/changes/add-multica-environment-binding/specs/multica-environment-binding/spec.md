## Purpose

Connect a Multica task's native checkout to a separately provisioned Odoo environment through small typed, inspectable and recoverable SDK and CLI operations.

## ADDED Requirements

### Requirement: Use native checkout through multica-py

The integration SHALL depend on a compatible public typed `multica-py` native repository checkout operation. That operation SHALL preserve Multica's active-task authentication, cwd, branch, reuse and ownership semantics. It SHALL NOT be replaced by direct HTTP, private storage edits, human-table parsing or a copied Git implementation. A supported one-path stdout result SHALL be decoded strictly and validated before adoption. `--fresh` SHALL NOT be used by this workflow.

Checkout and Odoo preparation SHALL be separate explicit phases because the actual returned checkout path is an input to the Odoo command. Native checkout failure/timeout SHALL NOT imply that no checkout was created. No general-purpose third checkout command or weakening of core ticket allocation SHALL be introduced.

#### Scenario: Native task checkout succeeds

- **WHEN** the public Multica SDK checkout operation succeeds inside a valid active task
- **THEN** its typed result identifies the actual checkout path, and the caller can pass that path into a separately captured Odoo preparation command without creating another worktree

#### Scenario: External worker lacks an active task context

- **WHEN** a worker attempts native checkout without the required task credential/context
- **THEN** the operation fails clearly without fabricating task identity or switching to an unregistered local clone

#### Scenario: Checkout output or outcome cannot be verified

- **WHEN** checkout returns malformed path output, times out or is interrupted
- **THEN** the caller receives a bounded sanitized failure/unknown outcome and Odoo preparation does not start automatically

### Requirement: Explicit project and task resolution

The extension SHALL expose typed project-link and context operations with CLI equivalents `project link`, `project show` and `context`. A project link SHALL contain non-secret core-project and Multica origin/workspace/project identity, not a duplicate business passport. Context SHALL validate the exact issue/run membership and execution host through public Multica SDK operations. Repository selection SHALL be explicit when ambiguous and SHALL NOT depend on task text, name similarity, branch names or list order.

Preparation/binding SHALL require the exact existing checkout to belong to the verified current or durable task directory on the same host and to the linked repository. Missing evidence SHALL yield an actionable unsupported/unverified result. Context inspection SHALL NOT create a project/resource/issue, enqueue a run or claim access-control/anonymization enforcement.

Host evidence SHALL come through a public typed Multica daemon-status result including server origin and per-workspace runtime IDs matched to the selected run. The required additive `multica-py` model/decoder work SHALL precede integration; missing fields SHALL NOT be replaced with inferred identity from a PID, hostname or path string. A loopback endpoint alone SHALL NOT prove shared filesystem access for forwarded/container daemons.

#### Scenario: Incorrect workspace or project

- **WHEN** issue membership does not match the explicit project link
- **THEN** preparation/binding is rejected before environment or binding mutation

#### Scenario: Same path text on another machine

- **WHEN** a run belongs to another runtime host even though its path string matches a local directory
- **THEN** preparation/binding fails rather than treating the path string as ownership evidence

#### Scenario: Selected run belongs to the local daemon

- **WHEN** public daemon status proves the matching server/workspace/runtime and filesystem context, and the issue's selected run supplies its absolute task directory
- **THEN** context can validate the exact repository checkout beneath that directory without a new host registry or direct daemon HTTP request

#### Scenario: Relative-only path or incomplete run inventory

- **WHEN** the supported API provides insufficient path/host evidence or required pagination cannot be completed
- **THEN** context reports unverified/incomplete and no local path is guessed from a display label

#### Scenario: Link changes after planning

- **WHEN** project-link bytes change between a planned update and execution
- **THEN** the captured update fails stale and preserves unrelated settings

### Requirement: Preparation is a thin COPY adoption boundary

`env prepare` and its public SDK command SHALL validate explicit Multica context and delegate to the captured public core adoption command. They SHALL accept the actual checkout path, explicit core project/base and exactly one supported COPY source. They SHALL NOT create another code checkout, bind implicitly, launch Odoo, reset code, alter project resources or start an agent. Missing predecessor capabilities SHALL fail before mutation with a compatibility diagnostic.

#### Scenario: Prepare from an exact retained backup

- **WHEN** a verified checkout is prepared from a compatible exact retained backup UUID
- **THEN** the core catalog/source checks, restore isolation and recovery contracts are reused and the environment UUID is returned without a remote backup request or automatic bind/start

#### Scenario: Prepare from a named source

- **WHEN** the caller explicitly selects the named staging source
- **THEN** only that configured source/credential and compatible base are passed to core COPY; no other source or default database is substituted

#### Scenario: Core preparation succeeds but next phase fails

- **WHEN** preparation returned an environment UUID but a later binding request fails
- **THEN** the environment is retained and the recommended recovery retries binding by that UUID, not checkout or restore

### Requirement: Local binding is explicit and non-destructive

The extension SHALL expose `env bind`, `env status`, and `env unbind` plus matching SDK operations and command siblings. Binding SHALL associate exact core project/environment IDs with Multica origin/workspace/project/issue/run/runtime IDs and checkout identity. It SHALL be owner-only ignored local metadata associated with the configured project, not a remote task-routing instruction or second environment catalog. It SHALL NOT be written into or uploaded from the daemon's checkout.

Rebinding the same association SHALL be a no-op; a verified subsequent run for the same issue and checkout SHALL be addable without duplicating the environment. Different project/issue associations SHALL conflict rather than be replaced silently. Concurrent binds SHALL use atomic persistence and a per-environment lock. One issue SHALL be allowed to reference multiple independently identified environments; branch name SHALL never be the key.

Unbind SHALL remove only extension binding metadata after identity revalidation. It SHALL NOT delete or stop the environment, code, database, backup, Multica task or project resource. None of these operations SHALL mutate Multica task status, assignment, project directory, execution mode or daemon lifecycle.

#### Scenario: Repeated binding

- **WHEN** two identical binding calls target the same environment and run
- **THEN** one association exists and the second result reports no change

#### Scenario: Another issue claims an existing binding

- **WHEN** bind is asked to replace a different issue association
- **THEN** it fails with a conflict and preserves the existing binding

#### Scenario: Unbind while Multica is unavailable

- **WHEN** the user requests removal of a known local binding while the Multica service cannot be reached
- **THEN** unbind can remove only that proven local metadata without contacting or deleting any remote resource

#### Scenario: Core removal races with binding

- **WHEN** the environment is removed while a binding is being persisted
- **THEN** the extension reports stale association on revalidation, never recreates the environment/artifact root and permits removal of only its own metadata

### Requirement: Status and failure results preserve useful evidence

Status SHALL return frozen bounded facts with observation time, binding state (`bound`, `unbound`, `stale`, `conflict`, `unavailable`), reason, environment identity and issue/run identity when known. Provisioning readiness and runtime/HTTP readiness SHALL remain separate. Missing/replaced checkout, wrong host and unavailable service SHALL NOT erase retained environment evidence or trigger repair.

Every finite mutation SHALL expose a non-mutating immutable preview and execute the same captured effects. JSON/TOON SHALL contain one final document with no progress/credential noise; Rich SHALL express the same facts. Failures SHALL retain completed-phase identities, sanitized reason and an existing next action; interruption SHALL preserve cancellation semantics and not restart the task or whole workflow.

#### Scenario: Stale external checkout

- **WHEN** Multica has garbage-collected the code directory
- **THEN** status identifies stale code and the retained environment UUID, and points to explicit core cleanup rather than recreating or deleting other resources

#### Scenario: Provisioned but stopped

- **WHEN** adoption is ready but Odoo has not been started
- **THEN** status does not claim service/HTTP readiness

#### Scenario: Inspect any mutating command

- **WHEN** project link, preparation, bind or unbind is previewed
- **THEN** the sanitized captured plan is available and no mutating child, filesystem write, database restore or remote mutation occurs

#### Scenario: Sensitive input or diagnostic

- **WHEN** any prerequisite command encounters task tokens or source passwords
- **THEN** no token/password reaches public plans, fingerprints, binding files, exception text or machine output

### Requirement: Caller retains orchestration and lifetime responsibility

The package SHALL provide finite operations without a scheduler, background watcher, task launcher, report workflow or cleanup daemon. It SHALL document that native code lifetime remains controlled by Multica, that local locking cannot prevent external GC, and that no persistent checkout lease is supplied. Existing core start/readiness/stop/remove operations SHALL remain separate caller-controlled steps.

#### Scenario: Caller implements a Temporal activity

- **WHEN** a worker composes the public primitives
- **THEN** it can save exact phase results and retry only an incomplete phase without depending on an extension-owned workflow engine
