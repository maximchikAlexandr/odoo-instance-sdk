## ADDED Requirements

### Requirement: Reuse native checkout through the typed Multica SDK

The integration SHALL use the public typed native-repository-checkout operation delivered by the supported `multica-py` revision that completes issue #93. It SHALL preserve scoped server/workspace configuration, active-task credentials, current task directory, explicit ref, timeout, cancellation, inspectable command capture, typed result, redaction, and native code ownership. It SHALL NOT use raw CLI argv, parse stdout/stderr, call private HTTP/storage, copy a process runner, or implement checkout registration. Forced fresh checkout SHALL NOT be part of this flow.

Checkout and Odoo preparation SHALL remain separately captured phases. No general-purpose extension checkout clone or core integrated checkout flag SHALL be added.

#### Scenario: Typed native checkout succeeds

- **WHEN** the supported typed checkout operation succeeds in a valid active task
- **THEN** its validated typed result supplies the native checkout identity/path for separately captured Odoo preparation without creating a second checkout

#### Scenario: Unknown checkout outcome

- **WHEN** typed checkout times out, is cancelled, or returns its defined unknown-outcome failure
- **THEN** preparation does not start automatically and the integration does not claim that no checkout was created

#### Scenario: Required typed operation is unavailable

- **WHEN** the installed SDK/CLI combination lacks the supported typed checkout contract
- **THEN** integration use fails before mutation with compatibility evidence and does not fall back to raw CLI invocation

### Requirement: Stateless verified task context

The extension SHALL expose a finite read-only context SDK operation and CLI equivalent. Inputs SHALL identify the selected core project/repository, checkout path, expected Multica project, issue, and run; existing scoped Multica configuration SHALL supply server/workspace credentials. It SHALL NOT persist a project-link file or infer identities from names, branches, task prose, or list order.

Context SHALL compose public typed issue/run and daemon-status operations from the supported `multica-py` revision. It SHALL verify issue/project/workspace/run membership, owning local daemon/runtime identity, repository identity, and containment beneath the run's absolute current/durable directory. It SHALL return frozen facts and observation time. Raw daemon commands, local JSON decoders, private transports, and reduced status models SHALL NOT be used.

#### Scenario: Matching local run

- **WHEN** exact membership, server/workspace/runtime, repository, and local filesystem/path evidence match
- **THEN** context returns the verified identifiers and checkout facts without mutation

#### Scenario: Wrong host or incomplete evidence

- **WHEN** identity conflicts, required typed fields are absent, run pagination is incomplete, or only a relative display path is available
- **THEN** context fails with an actionable unavailable/unverified result and preparation performs no mutation

#### Scenario: Forwarded endpoint

- **WHEN** a daemon is reachable but shared-local-filesystem evidence is absent
- **THEN** reachability and equal path strings alone do not authorize preparation

### Requirement: Thin Odoo preparation

The extension SHALL expose a preparation SDK operation, inspectable command sibling, and CLI equivalent. It SHALL perform bounded read-only context preflight and then delegate to the exact captured public core adoption command using an explicit compatible base and one explicit COPY source. It SHALL return the existing core environment result without starting Odoo or writing task associations.

The caller SHALL be able to retain the separate context result and environment UUID. No project-link CRUD, binding registry/history, binding locks, bind/unbind, extension status service, or orchestration framework SHALL be required. Core lifecycle operations SHALL remain usable without Multica.

#### Scenario: Exact retained backup

- **WHEN** a verified checkout is prepared from an exact compatible retained backup UUID
- **THEN** core COPY/provenance checks are reused and an environment UUID is returned without another remote backup request, implicit binding, or startup

#### Scenario: Named source

- **WHEN** the caller selects a named source
- **THEN** only that configured source/credential and explicit compatible base are used without default-source substitution

#### Scenario: Lost successful preparation response

- **WHEN** identical captured preparation inputs are retried against a ready matching environment
- **THEN** the existing UUID is returned without repeating checkout, download, restore, or dependency mutation

#### Scenario: Preparation fails after native checkout

- **WHEN** Odoo preparation fails
- **THEN** native code is preserved and core recovery identities are retained without distributed rollback or task-rerun claims

### Requirement: Preserve existing execution and output contracts

Public operations SHALL expose inspectable captured commands with delegating convenience methods. Preparation preflight MAY perform bounded observational reads, but preview SHALL NOT create a checkout, restore a database, or write files. Core revalidation SHALL protect local mutable inputs before effects. A preview SHALL NOT misrepresent remote task lifetime as locked.

Machine stdout SHALL contain one bounded sanitized JSON/TOON document; Rich SHALL express equivalent facts. Secrets SHALL NOT enter public plans, fingerprints, results, or diagnostics. The extension SHALL use existing supported SDK transport and output boundaries rather than a parallel execution/renderer framework.

#### Scenario: Inspect preparation

- **WHEN** preparation is previewed
- **THEN** observational context checks and the sanitized captured core plan are available without a mutating effect

#### Scenario: Provisioned but stopped

- **WHEN** preparation succeeds without an explicit runtime start
- **THEN** the result does not claim HTTP readiness

### Requirement: Caller owns orchestration and lifetime

The package SHALL provide finite operations only. Native checkout lifetime SHALL remain owned by Multica; scripts, skills, or workers SHALL own phase-result persistence and scheduling. No remote task/project-resource mutation, telemetry store, lease, cleanup daemon, role policy, or workflow engine SHALL be supplied by this change.

#### Scenario: Multica removes its code checkout

- **WHEN** the code owner removes a checkout after preparation
- **THEN** existing core diagnostics identify missing code and owned-only cleanup remains possible by environment UUID without contacting Multica

#### Scenario: Caller persists workflow results

- **WHEN** a caller saves the context result and environment UUID
- **THEN** it can inspect, start, stop, and remove the environment through core without an extension registry or repeated native checkout

### Requirement: Implementation requires dependency revalidation

Planning artifacts MAY be reviewed and published while dependencies are incomplete, but implementation SHALL NOT start until the core predecessor and all of `multica-py` #93 are implemented, verified, integrated/available, and inspected. After both complete, the Planner SHALL record their actual public APIs and versions, reconcile every affected planning artifact, rerun applicable validation and estimation, publish a new exact SHA, and obtain independent Plan Verifier approval.

An implementation parent created earlier SHALL remain in backlog. No WP child or implementation run SHALL start from a planning-only SHA.

#### Scenario: Only documentation is ready

- **WHEN** this OpenSpec is validated and approved while either implementation dependency remains incomplete
- **THEN** planning is ready but implementation remains prohibited

#### Scenario: Dependency is cancelled or partially implemented

- **WHEN** a dependency closes without its full required implementation or `multica-py` #93 supplies only checkout/status
- **THEN** the implementation gate remains closed

#### Scenario: Both dependencies complete

- **WHEN** both complete implementations are available
- **THEN** implementation remains prohibited until observed contracts are incorporated into a newly validated, independently approved, exact-SHA planning revision
