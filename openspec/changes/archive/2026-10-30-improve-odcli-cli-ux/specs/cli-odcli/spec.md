## MODIFIED Requirements

### Requirement: Stable machine output

The bounded structured leaf inventory SHALL be the current test-owned `PUBLIC_LEAF_CASES` entries classified as document-capable, including their declared dry-run variants; this change SHALL NOT create or freeze a second leaf-name list. Each bounded leaf SHALL accept its registered command-local `--format rich|json|toon`; `rich` SHALL be the default. Removed `--json` SHALL be an ordinary Click usage error with exit code `2`. `--format json` SHALL be the only JSON selector. Supplying `--json` with `--format` SHALL also be a Click usage error with exit code `2`. During normal execution, `run`, interactive `shell`, `psql`, and `logs --follow` SHALL remain raw-streaming and SHALL not emit document output or use a Rich live wrapper. Eligible spawning `run` and `shell` SHALL accept document-format options only together with `--dry-run`; those dry-run paths SHALL suppress native execution and emit one bounded plan document in Rich, JSON, or TOON. `psql --dry-run` SHALL remain an explicit plan-only exception that emits the shared sanitized native command plan without spawning; normal `psql` remains raw passthrough and SHALL continue to reject `--format` and `--json`.

The CLI SHALL define one CLI-only `OutputMode` with values `rich`, `json`, and `toon`. The mode and envelope types SHALL NOT become public SDK models or FastAPI response models. Each successful or failed bounded operation SHALL first build one JSON-safe CLI envelope v1 containing `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, and `warnings`; success SHALL contain equal `result` and `data`, while failure SHALL omit top-level `result` and `data` and SHALL contain stable `error.code` and sanitized `error.message`. `error` MAY additionally contain an operation-specific JSON-safe `details` field; failures without structured details SHALL omit it and retain their existing v1 shape.

JSON and TOON SHALL serialize that exact envelope without building format-specific result graphs. Decoding a TOON document with the selected strict decoder SHALL yield the same JSON value as decoding JSON output for the same operation. Machine modes SHALL emit exactly one UTF-8 document to stdout with no ANSI, prompt, status, progress, or external log text; diagnostics SHALL go to stderr. Renderer selection SHALL NOT change operation execution, exception mapping, or exit code. When a canonical bounded leaf or its supported dry-run has a valid explicit `--format json` or `--format toon`, parameter conversion, required-argument, range, choice, and typed `--fields` validation failures SHALL emit the same envelope with `error.code="usage_error"`, exit `2`, and no operation effects. The JSON and TOON documents SHALL encode the same envelope. Unknown commands, invalid or missing format selectors, and normal native/live modes outside the bounded contract SHALL retain Click's stderr usage output and exit `2`. The implementation SHALL reuse Click's command tree and the shared output pipeline and SHALL NOT parse argv a second time.

For `env remove`, `backup delete`, `db restore`, and `db drop`, JSON and TOON document modes SHALL never call `click.confirm`. Without `--yes`, they SHALL NOT execute mutation and SHALL emit exactly one sanitized failure envelope with `error.code="confirmation_required"` and exit code `1`. With `--yes`, JSON and TOON SHALL execute the same operation and normal success/failure mapping. Interactive Rich mode SHALL retain command-specific confirmation behavior. Dry-run SHALL never prompt and SHALL remain non-mutating.

Rich renderers SHALL remain adjacent to the concrete commands whose typed results they render. They MAY use `Table`, `Status`, `Progress`, and `Live` only when appropriate to the operation; they SHALL NOT introduce a generic renderer interface, registry, or DSL. `db stats` and `db bloat` SHALL render separate tables and indexes tables rather than one sparse combined table.

#### Scenario: JSON envelope
- **WHEN** `odcli env list --format json` executes
- **THEN** stdout contains exactly one versioned envelope and no progress or log text

#### Scenario: Supported machine usage error
- **WHEN** a canonical bounded leaf with valid explicit JSON or TOON format receives an invalid number, rejected range, unknown typed field, or missing required argument
- **THEN** stdout contains exactly one v1 failure document with `error.code="usage_error"`, exit code is `2`, stderr has no competing Rich usage document, and no operation runs

#### Scenario: Unsupported parse boundary stays native
- **WHEN** the command is unknown, the format selector is invalid or unresolved, or a native/live path has no supported document contract
- **THEN** Click writes its normal human usage error to stderr, exits `2`, and no machine document is invented

#### Scenario: TOON usage error is semantically equal to JSON
- **WHEN** the same supported validation failure is emitted once as JSON and once as TOON
- **THEN** strict TOON decoding and JSON decoding produce equal envelope values

#### Scenario: JSON alias preserves envelope v1
- **WHEN** `odcli env list --json` is invoked
- **THEN** Click exits `2` before the operation runs
- **AND** `odcli env list --format json` against the same frozen result emits envelope v1 with no ANSI or diagnostic text

#### Scenario: Machine mutation requires explicit confirmation
- **WHEN** `env remove`, `backup delete`, `db restore`, or `db drop` runs in JSON/TOON mode without `--yes` and without `--dry-run`
- **THEN** no prompt or mutation occurs, stdout contains one `confirmation_required` failure envelope, and the command exits `1`

#### Scenario: Native command dry-run supports every bounded format
- **WHEN** `odcli run --dry-run` or spawning `odcli shell --dry-run` is requested with `--format rich|json|toon`
- **THEN** output contains exactly one bounded plan with `dry_run=true` in the selected format, no child stream starts, and removed `--json` remains a usage error

#### Scenario: Canonical bounded inventory remains single-source
- **WHEN** the stable machine-output characterization gate compares the documented normal-execution leaves
- **THEN** they equal canonical `PUBLIC_LEAF_CASES` and no second bounded-leaf table is introduced

#### Scenario: Secrets remain redacted
- **WHEN** any bounded success, operation failure, or supported usage failure is emitted
- **THEN** every machine or Rich message redacts passwords, config bodies, sensitive environment values, captured secrets, and unsafe terminal control text

## ADDED Requirements

### Requirement: Expected CLI failures are bounded and actionable

The CLI SHALL classify invalid UTF-8 input and expected local filesystem access failures at the existing command boundary without catching unrelated programming errors. Rich mode SHALL emit a short sanitized reason and a safe next action without traceback. Supported JSON and TOON modes SHALL emit exactly one v1 failure envelope with a stable command-specific error code, sanitized message, and non-zero exit. The boundary SHALL NOT weaken access checks, disclose secret paths or values, or use blanket `except Exception` to suppress unknown faults.

#### Scenario: Invalid exec source is controlled
- **WHEN** `odcli exec` reads a source file containing invalid UTF-8 in Rich, JSON, or TOON mode
- **THEN** the command exits non-zero without traceback, child execution, ANSI in machine stdout, or leaked source bytes, and a machine mode emits exactly one failure document

#### Scenario: Update access denial is controlled
- **WHEN** `odcli update --check` encounters an expected permission denial in its local state path
- **THEN** the command exits non-zero with a sanitized actionable diagnostic or one machine failure document and does not weaken or change the denied permissions

### Requirement: Command help is discoverable and accurate

The root, every group, and every leaf SHALL accept `-h` and `--help` through the common Click context and exit `0` without runtime resolution or mutation. A `-h` token after a literal `--` on a native passthrough command SHALL remain a child argument. Help for `init`, `env create`, `db restore`, `run`, `exec`, and `test` SHALL contain one to three parseable examples and the applicable ordering, selector, input, default/config-source, database-mode, dry-run/read-only/mutation, confirmation, and native/live format constraints. The text SHALL state that integrated `init --venv` is optional and that `prepare-venv` is its explicit refresh/recovery path; it SHALL separately state that owned-checkout `env create --create-venv` is optional. Neither option becomes a new default. Help SHALL explain `exec -` stdin, literal `--` native arguments, and `env show`'s positional selector or cwd behavior without renaming commands or options.

#### Scenario: Short help works throughout the tree
- **WHEN** `-h` is invoked at root, a representative group, and a representative leaf
- **THEN** each prints help, exits `0`, and performs no catalogue, runtime, network, snapshot, or mutation work

#### Scenario: Native child short help is preserved
- **WHEN** a passthrough command receives literal `-- -h`
- **THEN** OdCLI passes `-h` to the child unchanged rather than opening OdCLI help

#### Scenario: Documented examples match registration
- **WHEN** the added examples are parsed or dry-run under isolated fixtures
- **THEN** their options, ordering, selectors, defaults, and safety claims match the registered command and no real mutation occurs
