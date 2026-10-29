## ADDED Requirements

### Requirement: SDK-first inspect command group

The CLI SHALL register `odcli inspect model MODEL`, `odcli inspect field MODEL FIELD`, and `odcli inspect ref XMLID`. Each leaf SHALL use the existing root context order, preserve explicit root `--env` precedence, and delegate to exactly one matching public `OdooInspector` operation. Inspect callbacks SHALL NOT construct Odoo scripts or require an HTTP server.

#### Scenario: Project-context model inspection

- **WHEN** `odcli inspect model res.partner --format json` runs inside an initialized default checkout with no HTTP server
- **THEN** the CLI resolves that project, delegates once to `instance.inspector.model_command("res.partner")`, and returns its bounded result

#### Scenario: Explicit environment field inspection

- **WHEN** `odcli --env ENV inspect field res.partner email --format toon` runs from outside the environment worktree
- **THEN** root environment selection wins and the CLI delegates once to the field inspector for that environment

#### Scenario: Inspect options remain narrow

- **WHEN** inspect help and parser contracts are inspected
- **THEN** no inspect leaf exposes command-local environment selection, dry-run, query/domain, field-value, write, SQL, recursive, or HTTP-server options

### Requirement: Inspect typed output parity and safety

Rich, JSON, and TOON inspect output SHALL project the same frozen SDK fields and missing-object state. Decoded JSON and TOON values SHALL be equal, and Rich SHALL add no registry or record data. Machine stdout SHALL contain one clean document; sanitized diagnostics SHALL remain on stderr. Renderer choice SHALL NOT change execution or exit status.

#### Scenario: Missing object has format parity

- **WHEN** the same missing model, field, or XML ID is inspected in Rich, JSON, and TOON modes
- **THEN** every mode reports `exists=false` with the same nullable metadata and success status
- **AND** decoded JSON and TOON results are equal

#### Scenario: Existing reference does not leak fields

- **WHEN** `odcli inspect ref base.user_admin` is rendered in every format
- **THEN** output is limited to XML ID, existence, model, record ID, and bounded display name
- **AND** it contains no login, password, token, configuration value, arbitrary record value, or callable representation

### Requirement: Canonical inspect leaf inventory

Each inspect leaf SHALL appear exactly once in test-owned `PUBLIC_LEAF_CASES` as bounded read-only with its matching `OdooInspector` SDK primitive, `requires_dry_run=false`, and a focused real-Odoo disposition covering live registry/XML-ID behavior. Production code SHALL NOT add a second command table or import the test inventory.

#### Scenario: Inspect leaves are fully classified

- **WHEN** canonical CLI characterization validates `PUBLIC_LEAF_CASES`
- **THEN** model, field, and ref each have exactly one row, one matching public SDK primitive, no CLI-only reason, no dry-run requirement, and non-empty focused E2E rationale

### Requirement: Opt-in test profile CLI projection

Top-level `odcli test [TARGET] --profile` SHALL pass `profile=true` into the existing `OdooTestSpec` and runner. Executed results SHALL add one profile object with totals and stable ordered entries. Without the option, the decoded field set SHALL remain unchanged and omit `profile`. Non-executed results SHALL NOT fabricate profile values.

#### Scenario: Profile is additive in all formats

- **WHEN** the same successful `odcli test sale --profile` run is rendered as Rich, JSON, and TOON
- **THEN** every mode contains the same totals and entry order from the typed profile, and decoded JSON/TOON results are equal

#### Scenario: Ordinary test result is unchanged

- **WHEN** `odcli test sale` executes without `--profile`
- **THEN** its result contains the pre-change fields and no `profile` key while selection, diagnostics, and exit behavior remain unchanged

#### Scenario: Profile combines with existing selection

- **WHEN** profile is requested with an explicit target or `--changed`
- **THEN** existing option validation and selection semantics remain authoritative and the selected native run alone supplies profile data

#### Scenario: Profile dry-run is one existing test plan

- **WHEN** `odcli test --changed --profile --dry-run --format json` resolves selected addons
- **THEN** the output contains the existing immutable test plan with the profile-aware fixed script, contains no fabricated profile result, and starts no Odoo process

### Requirement: Documentation of bounded diagnostics

README and CLI help SHALL show model, field, XML-ID, and test-profile examples and SHALL state the resolved project/environment context, rollback-only inspection, missing-object rule, native Odoo-stat source, and unprofiled compatibility. Documentation SHALL explicitly exclude arbitrary record inspection, writes, raw SQL, profile history, and custom profiling.

#### Scenario: User guidance states the safety boundary

- **WHEN** a developer reads inspect/test help and the SDK examples
- **THEN** they can run each supported operation and can see that inspection is bounded/read-only and profiling is opt-in/native without inferring a general ORM client or second runner
