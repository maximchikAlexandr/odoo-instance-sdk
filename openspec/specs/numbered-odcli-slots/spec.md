# numbered-odcli-slots Specification

## Purpose
TBD - created by archiving change add-numbered-odcli-slots. Update Purpose after archive.
## Requirements
### Requirement: One shared alternate-tool lifecycle
Numbered and hot-fix launchers SHALL use one package-owned implementation for deterministic identity paths, uv installation, exact provenance, selector-capability probing, manifest serialization, shim rendering, lifecycle locking, bounded inspection, and fail-closed tool/state removal. The private numbered coordinator and hot-fix skill wrapper SHALL contain only their distinct policy and SHALL NOT implement or copy those mechanics.

#### Scenario: Both launcher kinds use one lifecycle implementation
- **WHEN** a numbered installation and a reviewed hot fix are installed, invoked, inspected, and removed
- **THEN** both pass through the same install/probe/manifest/shim/lock/remove implementation while their adapters apply only checkout/allocation policy or PR/reviewer/retirement policy

#### Scenario: Duplicate lifecycle mechanics are rejected
- **WHEN** architecture checks inspect private numbered tooling, public CLI modules, and hot-fix skill surfaces
- **THEN** they fail if any second uv installer, provenance verifier, capability probe, launcher renderer, lifecycle-lock implementation, or alternate-tool cleanup algorithm exists outside the shared module

### Requirement: Numbered management is private service tooling
The public `odcli` Click tree and package console scripts SHALL NOT expose install, list, replace, remove, or allocation commands for numbered installations. An instruction-only developer skill SHALL invoke a repository-owned service wrapper outside `.agents/skills/**`; that wrapper SHALL resolve the trusted canonical interpreter and invoke one private package coordinator. The user-agent OdCLI skill SHALL NOT describe or invoke numbered management. The coordinator SHALL require a full lowercase 40-character SDK SHA and SHALL return bounded machine-readable identity evidence without printing secrets.

#### Scenario: Public help has no slot manager
- **WHEN** an operator inspects root and nested public CLI help
- **THEN** no `slot` group or numbered lifecycle management leaf is present

#### Scenario: Service wrapper invokes the trusted package
- **WHEN** an OdCLI developer follows the developer skill to create, list, inspect, or remove a numbered installation
- **THEN** it validates canonical executable provenance before invoking the private package coordinator and emits one bounded result

#### Scenario: User skill does not expose developer management
- **WHEN** an agent loads the ordinary OdCLI user skill
- **THEN** it receives no numbered-management workflow or executable entrypoint and is directed only through supported user and hot-fix operations

### Requirement: Skill executable code has one repository owner
The user and developer skill directories SHALL contain no Python executable helper, copied lifecycle implementation, or forwarding script. Every executable helper referenced by either skill SHALL live under the canonical repository-owned `scripts/odcli_agent_tools/` path, and references, tests, architecture inventories and documentation SHALL resolve to that path. Moving helpers SHALL preserve their trusted-interpreter, provenance, bounded-output and failure contracts.

#### Scenario: Skill directories are instruction only
- **WHEN** architecture checks inventory `.agents/skills/**`
- **THEN** no Python executable exists there and both skills reference only canonical repository-owned helper paths

#### Scenario: Relocated helpers preserve behavior
- **WHEN** review, hot-fix and numbered helper tests invoke their new paths
- **THEN** the same validated operations, redaction, exit status and failure behavior are observed without a duplicate or compatibility forwarding copy

### Requirement: Allocation uses the smallest wholly free positive number
The private coordinator SHALL hold one global allocation lock while scanning positive canonical decimal identities from `1`. It SHALL select the first number for which the launcher, selected state root, manager/reservation path, and configured linked checkout are all absent. Any symlink, partial path, invalid marker, legacy manifest, or lone artifact in any dimension SHALL mark that number occupied and SHALL NOT be adopted, repaired, overwritten, or deleted.

#### Scenario: Gaps and orphans are skipped
- **WHEN** number `1` has only an orphaned state root, number `2` has a valid installation, and every path for number `3` is absent
- **THEN** creation reserves number `3`, reports the occupied earlier identities, and changes nothing under numbers `1` and `2`

#### Scenario: Concurrent creators cannot claim one number
- **WHEN** two service processes allocate concurrently against the same home and checkout parent
- **THEN** their completed installations have distinct numbers and neither observes a partially published identity as free

### Requirement: Numbered tool, checkout, and Python environment are one linked identity
For number `N`, the coordinator SHALL bind launcher `odcli-N`, selected root `<real-home>/.odcli-N`, manager metadata, checkout `<configured-checkout-parent>/odcli-N`, and exactly one caller-owned-checkout environment returned by the existing SDK adoption lifecycle. The fixed numbered manifest SHALL record the verified checkout path, source repository/SHA, environment identity/path, audited dependency-lock digest, and resolved interpreter path; hot-fix manifests SHALL NOT contain checkout or numbered-environment fields. Real `HOME` and Git, SSH, uv, and unrelated user configuration SHALL remain unchanged.

#### Scenario: Fresh numbered identity is published
- **WHEN** a valid exact SDK SHA is created with a clean configured reference checkout and all required external inputs
- **THEN** the coordinator clones `odcli-N`, passes it to existing environment `adopt_command()`/`adopt()` for config/port/COPY DB/catalogue/owned-venv creation, verifies the returned environment plus checkout/tool provenance, publishes one linked manifest/launcher, and leaves every canonical or neighboring identity unchanged

#### Scenario: Tool provenance fails after checkout staging
- **WHEN** the staged SDK tool does not match the requested repository and SHA
- **THEN** no launcher or ready manifest is published; if adoption already returned ready, existing environment `remove()` cleans that environment once before the coordinator deletes only checkout/tool artifacts proven to belong to the reservation, while any cleanup failure retains linkage for retry

### Requirement: Checkout coordination delegates the environment lifecycle
The SDK private coordinator SHALL implement only global numbered allocation/reservation, clean reference/origin verification, local no-hardlink clone, origin rewrite, fixed marker/hash creation, external-checkout inspection, manifest linkage, and contained checkout deletion after environment cleanup. It SHALL pass the caller-owned checkout and GitLab-owned `ProjectConfig`/`EnvironmentCheckoutOptions` inputs to existing environment `adopt_command()`/`adopt()` for generated config, port, COPY database/backup restore, catalogue, owned venv, verification and recovery. It SHALL call existing environment `remove()` exactly once for DB/backup/config/lock/venv cleanup before deleting the external checkout. It SHALL NOT implement or copy config rewrite, port allocation, restore, DB drop, catalogue mutation, venv, or environment-cleanup mechanics. If a required option is proven unavailable, only the smallest typed extension of the existing environment API with a focused test is permitted. Secrets SHALL NOT be printed, returned, stored in the numbered manifest, or committed.

#### Scenario: GitLab wrapper supplies concrete policy
- **WHEN** the Binta wrapper invokes create
- **THEN** it supplies every required existing-API input, adoption validates and applies them, and no Binta path, source, database name, interpreter, lock or secret is guessed by the coordinator

#### Scenario: Unsupported configuration fails closed
- **WHEN** a required project/reference path, explicit base/source/COPY target, requested port, PostgreSQL endpoint, base Python, dependency lock/digest, Odoo bin, or private configuration input is missing or inconsistent
- **THEN** creation fails before publishing a numbered identity and preserves foreign/reference data

#### Scenario: A numbered checkout cannot use a shared Python environment
- **WHEN** the wrapper proposes an interpreter environment already linked to canonical, another numbered checkout, or a hot-fix identity
- **THEN** creation fails before runtime or publication instead of adopting or writing to that environment

### Requirement: Numbered cleanup is selective and fail closed
Removal SHALL take the global allocation lock and selected lifecycle lock, re-read manifest/marker/linkage, and reject symlink/path escape, dirty or unpushed external checkout, origin/marker mismatch, running launcher, or foreign ownership before mutation. It SHALL then invoke existing environment `remove()` for the recorded environment; that path SHALL own runtime/config/lock/interpreter/database evidence and DB/backup/config/lock/venv cleanup while preserving the caller-owned checkout. Only after successful environment removal SHALL the coordinator delete the marked checkout and delegate launcher/tool/state removal to the shared lifecycle. It SHALL NOT independently drop a database or delete generated config, dependency lock, venv, backup, filestore, or catalogue data. A failed environment removal or checkout deletion SHALL retain linkage for bounded retry and preserve every neighbor/reference/persistent asset.

#### Scenario: One clean numbered identity is removed
- **WHEN** two numbered identities exist and one idle identity has a clean pushed checkout with matching markers/configuration
- **THEN** existing environment removal deletes only its recorded DB/backup/config/lock/owned venv, then the coordinator deletes only its marked checkout, and the shared alternate lifecycle removes only its launcher/tool/manager/selected root

#### Scenario: Dirty checkout blocks removal
- **WHEN** the selected linked checkout has an uncommitted file or unpushed commit
- **THEN** removal changes no database, checkout, launcher, tool, manifest, lock, state root, or neighbor

### Requirement: Hot-fix identity remains isolated without checkout policy
The existing `odcli-fix-ISSUE` workflow SHALL retain its positive issue identity, exact reviewed SHA, PR linkage, isolated `~/.odcli-fix-ISSUE`, shared lifecycle, and merge/ancestry/issue/branch retirement gates. It SHALL NOT allocate or manage numbered checkouts, ports, databases, or filestores.

#### Scenario: Hot-fix creation remains thin
- **WHEN** a reviewed hot-fix revision is installed
- **THEN** the skill applies GitHub/reviewer policy and delegates lifecycle mechanics to the shared engine without invoking numbered checkout allocation

### Requirement: Concurrent roots and external resources remain isolated
Each launcher SHALL derive all SDK-owned global paths from its selected root for its full process lifetime. Each numbered checkout SHALL also have its own project checkout, SDK-owned Python environment, database, filestore, and HTTP port as supplied or parameterized by the wrapper. Numbered runtimes SHALL resolve their recorded environment interpreter and SHALL NOT import from or write into another identity's environment. Only the PostgreSQL server, read-only Odoo source, and source backup MAY be shared dependencies; numbered cleanup SHALL NOT stop, write to, or delete them.

#### Scenario: Two numbered and two hot-fix revisions coexist
- **WHEN** canonical, two numbered and two hot-fix revisions perform incompatible catalogue migrations while the numbered checkouts run on distinct Python environments, databases, filestores, and ports
 - **THEN** each launcher accesses only its selected global root, each numbered runtime resolves a distinct `sys.executable` and site-package root and accesses only its linked project resources, and all nonselected roots/resources retain their before-run digests

### Requirement: Numbered creation proves a complete isolated lifecycle

The repository-owned numbered workflow SHALL prove `create -> list -> inspect -> remove` against a caller-owned clone whose origin is transport-equivalent to the configured project and whose COPY archive is acquired from a named remote. Successful creation SHALL publish the smallest wholly free numbered identity only after the local target database, isolated filestore, checkout, owned Python environment, launcher, manifest, and catalogue environment satisfy their existing postconditions. Removal SHALL delete only those proven owned resources while preserving the reference checkout, source instance and database, source backup, canonical installation, and neighboring numbered identities.

#### Scenario: Complete lifecycle succeeds with equivalent remotes
- **WHEN** the configured project and numbered clone use equivalent scp-like and `ssh://` Git origins and all explicit project inputs are valid
- **THEN** create SHALL reach ready, list and inspect SHALL report the same bounded identity and resource linkage, and remove SHALL eliminate that identity's database, filestore, checkout, environment, launcher, manifest, and selected root

#### Scenario: Non-equivalent remote is rejected without partial resources
- **WHEN** the numbered clone resolves to a different canonical repository identity
- **THEN** create SHALL fail before adoption mutation, publish no launcher or ready manifest, and leave no newly owned database, filestore, checkout, environment, or tool artifacts after bounded cleanup

#### Scenario: Restore failure leaves no untracked partial slot
- **WHEN** backup acquisition, auxiliary startup, restore, or local target verification fails during numbered creation
- **THEN** no ready numbered identity SHALL appear in list or inspect, all safely proven partial owned resources SHALL be removed, and any unresolved destructive ownership SHALL remain explicitly linked for existing recovery rather than being silently deleted

#### Scenario: Neighbor and reference assets remain unchanged
- **WHEN** one numbered lifecycle succeeds or fails while a canonical installation, reference project, source database and backup, and another numbered identity exist
- **THEN** before/after evidence SHALL show those nonselected assets retain their identities and contents
