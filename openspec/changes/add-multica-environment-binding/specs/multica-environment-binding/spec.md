## Purpose

Provide verified task context and prepare an isolated Odoo environment on a native Multica checkout without duplicating task routing, configuration or persistent associations.

## ADDED Requirements

### Requirement: Reuse native checkout through the existing Multica SDK

The integration SHALL use the existing public bounded Multica SDK command API for native repository checkout. It SHALL preserve active-task credentials, cwd, explicit ref, timeout, cancellation and native code ownership. It SHALL decode the single-path stdout contract strictly and SHALL NOT parse human tables, use private HTTP/storage or copy a subprocess runner. Forced fresh checkout SHALL NOT be part of this flow.

Checkout and Odoo preparation SHALL remain separately captured phases. No general-purpose extension checkout command or core integrated checkout flag SHALL be added.

#### Scenario: Native checkout succeeds

- **WHEN** a native checkout command succeeds in a valid active task
- **THEN** its validated absolute Git checkout path is available for a separately captured Odoo preparation, without creating a second checkout

#### Scenario: Malformed output or unknown outcome

- **WHEN** output is empty, multiline, redacted or invalid, or the command times out or is interrupted
- **THEN** Odoo preparation does not start automatically, diagnostics remain sanitized and the caller is not told that no checkout was created

#### Scenario: Worker lacks active task credentials

- **WHEN** a worker attempts native checkout without the required task context
- **THEN** it fails explicitly instead of inventing a task or creating an unregistered clone

### Requirement: Stateless verified task context

The extension SHALL expose a finite read-only context SDK operation and CLI equivalent. Inputs SHALL identify the selected core project/repository, checkout path, expected Multica project, issue and run; existing scoped Multica configuration SHALL supply server/workspace credentials. It SHALL NOT persist a project-link file or infer identities from names, branches, task prose or list order.

Context SHALL verify issue/project/workspace/run membership, owning local daemon runtime identity, repository identity and containment beneath the run's absolute current/durable directory. It SHALL return frozen facts and observation time. Existing typed issue/run operations and a narrow decoder of public daemon-status JSON SHALL provide the evidence, without requiring new upstream typed wrappers.

#### Scenario: Matching local run

- **WHEN** exact membership, server/workspace/runtime and local filesystem/path evidence match
- **THEN** context returns the verified identifiers and checkout facts without mutation

#### Scenario: Wrong host or incomplete evidence

- **WHEN** host/project identity conflicts, daemon JSON lacks required fields, run pagination is incomplete or only a relative display path is available
- **THEN** context fails with an actionable unavailable/unverified reason and preparation does not mutate anything

#### Scenario: Forwarded endpoint

- **WHEN** a daemon is reachable over loopback but shared local filesystem evidence is absent
- **THEN** reachability and equal path strings alone do not authorize preparation

### Requirement: Thin Odoo preparation

The extension SHALL expose a preparation SDK operation, its inspectable command sibling and `env prepare`. It SHALL perform read-only context preflight then delegate to the exact captured public core adoption command using an explicit compatible base and one explicit COPY source. It SHALL return the existing core environment result, without starting Odoo or writing task associations.

The caller SHALL be able to retain the separate context result and environment UUID. No project-link CRUD, binding registry/history, binding locks, bind/unbind or extension status service SHALL be required. Core lifecycle operations SHALL remain usable without Multica.

#### Scenario: Exact retained backup

- **WHEN** a verified checkout is prepared from an exact compatible retained backup UUID
- **THEN** core COPY/provenance checks are reused and an environment UUID is returned without another remote backup request, implicit binding or startup

#### Scenario: Named source

- **WHEN** the caller selects the named staging source
- **THEN** only that configured source/credential and explicit compatible base are used, without default-source substitution

#### Scenario: Lost successful preparation response

- **WHEN** identical captured preparation inputs are retried against a ready matching environment
- **THEN** the existing UUID is returned without repeating checkout, download, restore or dependency mutation

#### Scenario: Preparation fails after native checkout

- **WHEN** Odoo preparation fails
- **THEN** native code is preserved and core recovery identities are retained; no distributed rollback or task rerun is claimed

### Requirement: Preserve existing execution and output contracts

Public operations SHALL expose inspectable captured commands with delegating convenience methods. Preparation preflight MAY perform bounded observational reads, but preview SHALL NOT create a checkout, restore a database or write files. Core revalidation SHALL protect local mutable inputs before execution. Remote task lifetime SHALL NOT be misrepresented as locked by a preview.

Machine stdout SHALL contain one bounded sanitized JSON/TOON document; Rich SHALL express equivalent facts. Secrets SHALL NOT enter public plans, fingerprints, results or diagnostics. The extension SHALL use existing supported SDK transport and output boundaries rather than a parallel execution/renderer framework.

#### Scenario: Inspect preparation

- **WHEN** preparation is previewed
- **THEN** observational context checks and the sanitized captured core plan are available without any mutating effect

#### Scenario: Provisioned but stopped

- **WHEN** preparation succeeds without an explicit runtime start
- **THEN** the result does not claim HTTP readiness

### Requirement: Caller owns orchestration and lifetime

The package SHALL provide finite operations only. Native checkout lifetime SHALL remain owned by Multica; scripts/skills/workers SHALL own phase-result persistence and scheduling. No remote task/project-resource mutation, telemetry store, lease, cleanup daemon, role policy or workflow engine SHALL be supplied by this change.

#### Scenario: Multica removes its code checkout

- **WHEN** the code owner removes a checkout after preparation
- **THEN** existing core diagnostics identify missing code and owned-only cleanup remains possible by environment UUID without contacting Multica

#### Scenario: Caller persists workflow results

- **WHEN** a script or activity saves the context result and environment UUID
- **THEN** it can inspect/start/stop/remove the environment through core without an extension registry or a repeated native checkout
