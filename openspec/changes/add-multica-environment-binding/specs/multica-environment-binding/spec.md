## ADDED Requirements

### Requirement: Reuse native checkout through the typed Multica SDK

The integration SHALL use `MulticaClient.repositories.checkout_command(url, *, ref=..., fresh=False, options=...)` and its delegating `checkout()` sibling from exact `multica-py` PR #95 merge revision, consuming frozen `RepositoryCheckoutResult.path`. It SHALL preserve scoped server/workspace configuration, active-task credentials, current task directory, explicit ref, timeout, cancellation, inspectable command capture, typed result, redaction, and native code ownership. It SHALL NOT use raw CLI argv, parse stdout/stderr, call private HTTP/storage, copy a process runner, or implement checkout registration. Forced fresh checkout SHALL NOT be part of this flow.

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

Context SHALL compose public typed project, project-resource, issue/run operations with `MulticaClient.daemon.status_command()/status()` and frozen `DaemonStatus` from exact `multica-py` PR #95 merge revision. It SHALL require `Issue.project_id` to equal the selected `Project.id`, a complete public `Project.resources` page, and exactly one matching typed `github_repo` resource whose `GithubRepoResourceRef.url` identifies the selected core repository. It SHALL separately verify workspace/issue/run membership, owning local daemon/runtime identity, and containment beneath the run's absolute current/durable directory. Populated TaskRun project identity snapshots SHALL be consistent, but their absence SHALL NOT fail otherwise complete authoritative Issue/Project evidence. It SHALL return frozen facts and observation time. Raw daemon commands, local JSON decoders, private transports, and reduced status models SHALL NOT be used.

#### Scenario: Matching local run

- **WHEN** exact membership, server/workspace/runtime, repository, and local filesystem/path evidence match
- **THEN** context returns the verified identifiers and checkout facts without mutation

#### Scenario: TaskRun omits duplicated project snapshot

- **WHEN** the exact TaskRun omits `project_id` or `project_resources`, while the issue identifies the selected project, the complete public Project resource collection identifies exactly the selected repository, and run/workspace/runtime/path plus daemon evidence match
- **THEN** context returns the verified identifiers and checkout facts without weakening any run, host, runtime, or path check

#### Scenario: Project repository evidence is unavailable

- **WHEN** the public Project resource page is incomplete, has no matching repository, has multiple matches, or conflicts with the selected core repository, or a populated TaskRun project snapshot conflicts with authoritative Issue/Project evidence
- **THEN** context fails before mutation and does not infer repository identity from TaskRun absence, prose, names, list order, or local paths

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

### Requirement: Blocker repair requires exact planning approval

The implementation SHALL use the blocker-revised planning revision that records integrated Odoo Instance SDK PR #110 and complete `multica-py` PR #95 public contracts, the authoritative public Issue/Project resource identity contract, selected base, compatible version evidence, recomputed estimate properties and validated delivery topology. Its exact SHA SHALL be pushed unchanged and independently approved by Plan Verifier before `WP-04` resumes.

`WP-04` SHALL remain blocked and its current candidate SHALL remain unaccepted. Manager/WP Delivery SHALL NOT change production code or OpenSpec from an earlier planning SHA or outside the planning flow.

#### Scenario: Blocker revision is not approved

- **WHEN** the task-4.2 blocker revision exists but lacks independent exact-SHA approval
- **THEN** `WP-04` remains blocked, its candidate remains unaccepted, and Manager/WP Delivery performs no production or OpenSpec repair

#### Scenario: Blocker-revision exact SHA is approved

- **WHEN** Plan Verifier confirms that the pushed exact SHA matches the fully validated blocker-revision package and its recorded compatibility evidence
- **THEN** Plan Verifier may update the graph contract and Manager may resume `WP-04` under the revised task 4.2 acceptance contract

#### Scenario: Contract drift appears after approval

- **WHEN** the selected base or dependency API/version no longer matches the approved evidence in a scope-, topology-, or threshold-affecting way
- **THEN** implementation remains or becomes closed until a new planning revision is validated and independently approved
