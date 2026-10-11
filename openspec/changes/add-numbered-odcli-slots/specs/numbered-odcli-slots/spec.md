## ADDED Requirements

### Requirement: One shared alternate-tool lifecycle
Numbered and hot-fix launchers SHALL use one package-owned implementation for deterministic identity paths, uv installation, exact provenance, selector-capability probing, manifest serialization, shim rendering, lifecycle locking, bounded inspection, and fail-closed tool/state removal. The private numbered coordinator and hot-fix skill wrapper SHALL contain only their distinct policy and SHALL NOT implement or copy those mechanics.

#### Scenario: Both launcher kinds use one lifecycle implementation
- **WHEN** a numbered installation and a reviewed hot fix are installed, invoked, inspected, and removed
- **THEN** both pass through the same install/probe/manifest/shim/lock/remove implementation while their adapters apply only checkout/allocation policy or PR/reviewer/retirement policy

#### Scenario: Duplicate lifecycle mechanics are rejected
- **WHEN** architecture checks inspect private numbered tooling, public CLI modules, and hot-fix skill surfaces
- **THEN** they fail if any second uv installer, provenance verifier, capability probe, launcher renderer, lifecycle-lock implementation, or alternate-tool cleanup algorithm exists outside the shared module

### Requirement: Numbered management is private service tooling
The public `odcli` Click tree and package console scripts SHALL NOT expose install, list, replace, remove, or allocation commands for numbered installations. A checked-in service/skill wrapper SHALL resolve the trusted canonical interpreter and invoke one private package coordinator. The coordinator SHALL require a full lowercase 40-character SDK SHA and SHALL return bounded machine-readable identity evidence without printing secrets.

#### Scenario: Public help has no slot manager
- **WHEN** an operator inspects root and nested public CLI help
- **THEN** no `slot` group or numbered lifecycle management leaf is present

#### Scenario: Service wrapper invokes the trusted package
- **WHEN** the checked-in wrapper creates, lists, inspects, or removes a numbered installation
- **THEN** it validates canonical executable provenance before invoking the private package coordinator and emits one bounded result

### Requirement: Allocation uses the smallest wholly free positive number
The private coordinator SHALL hold one global allocation lock while scanning positive canonical decimal identities from `1`. It SHALL select the first number for which the launcher, selected state root, manager/reservation path, and configured linked checkout are all absent. Any symlink, partial path, invalid marker, legacy manifest, or lone artifact in any dimension SHALL mark that number occupied and SHALL NOT be adopted, repaired, overwritten, or deleted.

#### Scenario: Gaps and orphans are skipped
- **WHEN** number `1` has only an orphaned state root, number `2` has a valid installation, and every path for number `3` is absent
- **THEN** creation reserves number `3`, reports the occupied earlier identities, and changes nothing under numbers `1` and `2`

#### Scenario: Concurrent creators cannot claim one number
- **WHEN** two service processes allocate concurrently against the same home and checkout parent
- **THEN** their completed installations have distinct numbers and neither observes a partially published identity as free

### Requirement: Numbered tool, checkout, and Python environment are one linked identity
For number `N`, the coordinator SHALL bind launcher `odcli-N`, selected root `<real-home>/.odcli-N`, manager metadata, checkout `<configured-checkout-parent>/odcli-N`, and exactly one SDK-owned Python environment created through the existing environment lifecycle. The fixed numbered manifest SHALL record the verified checkout path, source repository/SHA, environment identity/path, audited dependency-lock digest, and resolved interpreter path; hot-fix manifests SHALL NOT contain checkout or numbered-environment fields. Real `HOME` and Git, SSH, uv, and unrelated user configuration SHALL remain unchanged.

#### Scenario: Fresh numbered identity is published
- **WHEN** a valid exact SDK SHA is created with a clean configured reference checkout and all required external inputs
- **THEN** the coordinator clones and configures `odcli-N`, creates and verifies its distinct hash-locked SDK-owned Python environment, verifies checkout and tool provenance, publishes one linked manifest/launcher, and leaves every canonical or neighboring identity unchanged

#### Scenario: Tool provenance fails after checkout staging
- **WHEN** the staged SDK tool does not match the requested repository and SHA
- **THEN** no launcher or ready manifest is published and cleanup removes only checkout/tool artifacts proven to belong to that reservation

### Requirement: Checkout mechanics are bounded and configuration-owned
The SDK private coordinator SHALL implement only the inspected reusable mechanics: clean reference/origin verification, local no-hardlink clone, origin rewrite, bounded loopback-port selection, deterministic database/path derivation, private configuration copy, exact bounded rewrites, marker/hash creation, restore, inspection, dirty/unpushed/runtime checks, database drop, and contained deletion. It SHALL compose, not duplicate, the existing SDK-owned environment create/hash-lock/verification/cleanup path. All reference paths, backup, port bounds, database prefix, expected PostgreSQL endpoint, base Python, audited dependency-lock path/digest, Odoo bin path, and private configuration sources SHALL be supplied and owned by the GitLab wrapper. Secrets SHALL NOT be printed, returned, stored in the numbered manifest, or committed.

#### Scenario: GitLab wrapper supplies concrete policy
- **WHEN** the Binta wrapper invokes create
- **THEN** it supplies every required environment value, the SDK coordinator validates them before mutation, and no Binta path, backup, port range, database name, or secret is guessed by an SDK default

#### Scenario: Unsupported configuration fails closed
- **WHEN** a required path, port bound, database prefix, source hash, PostgreSQL endpoint, base Python, dependency lock/digest, or private configuration input is missing or inconsistent
- **THEN** creation fails before publishing a numbered identity and preserves foreign/reference data

#### Scenario: A numbered checkout cannot use a shared Python environment
- **WHEN** the wrapper proposes an interpreter environment already linked to canonical, another numbered checkout, or a hot-fix identity
- **THEN** creation fails before runtime or publication instead of adopting or writing to that environment

### Requirement: Numbered cleanup is selective and fail closed
Removal SHALL take the global allocation lock and the selected lifecycle lock, re-read the manifest and checkout marker after locking, and reject symlinks, path escape, dirty/unpushed checkout work, running launcher or Odoo runtime, configuration/hash drift, dependency-lock drift, environment/interpreter mismatch, database mismatch, or foreign ownership. After all preconditions pass, it SHALL use the existing SDK environment cleanup to delete only the recorded owned venv, drop only the recorded disposable database, delete only the linked checkout, and delegate launcher/tool/state removal to the shared lifecycle. It SHALL preserve every other Python environment, the reference checkout, read-only Odoo source, source backup, persistent and neighboring databases/filestores, shared PostgreSQL, canonical root, and all neighboring numbered/hot-fix identities.

#### Scenario: One clean numbered identity is removed
- **WHEN** two numbered identities exist and one idle identity has a clean pushed checkout with matching markers/configuration
- **THEN** only its owned Python environment, database, checkout, launcher, tool, manager metadata, lock, and selected root are removed

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
