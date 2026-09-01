## ADDED Requirements

### Requirement: Universal command-local dry-run

Every CLI leaf that starts a child process or mutates local state SHALL accept the shared command-local `--dry-run` option unless it has a tested non-previewable exception with a concrete reason. Native transport during normal execution is not such an exception: eligible foreground, interactive, and long-running spawning leaves, including `run` and spawning `shell`, SHALL build their SDK/use-case command exactly once; normal mode calls `.run()` with native streams, while dry-run emits the same command's bounded plan and SHALL NOT call `.run()`. For raw-stream `run` and spawning `shell`, `--dry-run` SHALL accept `--format rich|json|toon` with `rich` as the default, and `--json` SHALL be equivalent to `--format json` under the same conflict rules as bounded leaves. Supplying `--format` or `--json` to either raw-stream leaf without `--dry-run` SHALL be a Click usage error with exit code `2` before SDK resolution, command construction, or process launch.

Purely read-only leaves SHALL NOT gain `--dry-run` unless their process plan is an explicitly documented useful preview. The repository SHALL maintain a tested inventory of eligible leaves and exceptions so new mutating/spawning commands cannot omit the option.

#### Scenario: Valid dry-run

- **WHEN** an eligible leaf is invoked with `--dry-run`
- **THEN** it exits `0`, performs no planned effect, does not prompt, and emits the command's redacted plan with `dry_run=true`

#### Scenario: Invalid plan in dry-run

- **WHEN** command construction returns an unresolved, unsafe, or stale planning error
- **THEN** dry-run exits non-zero through the shared typed error document
- **AND** no planned effect or prompt occurs

#### Scenario: New mutating command omits dry-run

- **WHEN** a new CLI leaf is classified as process-spawning or state-mutating but has neither the shared option nor a documented exception
- **THEN** the CLI architecture contract test fails

### Requirement: One typed bounded-output boundary

Every bounded CLI result SHALL first construct one immutable typed output document. JSON and TOON SHALL serialize the same JSON-safe projection, and Rich SHALL be a pure projection of the same document/result. Only the shared output adapter SHALL own bounded stdout/stderr writes, serializer selection, diagnostics, and exit mapping; command callbacks and Rich renderers SHALL NOT write directly or branch on output mode.

#### Scenario: Same result in three modes

- **WHEN** one frozen successful or failed result is emitted as Rich, JSON, and TOON
- **THEN** every mode represents the same command, context, provenance, dry-run state, warnings, result/error, and exit semantics
- **AND** decoded JSON and TOON values are equal

#### Scenario: Callback implementation is inspected

- **WHEN** an architecture test inspects bounded Click callbacks
- **THEN** callbacks contain no subprocess argv construction, serializer selection, direct output call, or output-mode branch

### Requirement: Inspectable plan rendering

Dry-run Rich output SHALL show ordered process/action steps, complete multiline executable stdin/script blocks after redaction, observations, warnings, mutation/interactive/long-running classification, and fingerprint. JSON and TOON SHALL expose the same machine fields and argv arrays.

#### Scenario: Multiline Odoo stdin

- **WHEN** a dry-run plan contains multiline Odoo shell source
- **THEN** Rich preserves the block's line structure
- **AND** JSON and TOON contain the same redacted source as a machine field

### Requirement: Explicit native-stream exceptions

Native Odoo inherited stdin/stdout/stderr, interactive shell, `logs --follow`, and documented JSONL streaming SHALL remain explicit stream transports rather than bounded documents during normal execution. Native execution paths that start child processes SHALL still use captured command steps and the shared process executor. Their direct transport ownership SHALL be covered by a minimal source-level allowlist, but that allowlist SHALL NOT exempt an eligible spawning leaf from bounded dry-run plan emission.

`logs --follow` is non-previewable because it is a read-only subscription whose future stream has no finite executable/mutating snapshot to inspect. A documented JSONL stream MAY be non-previewable only when it is likewise read-only and has no finite child-process or mutation plan; the canonical CLI inventory SHALL record that concrete reason. A spawning shell is previewable even though its normal transport is interactive.

#### Scenario: Foreground run without dry-run

- **WHEN** `odcli run` executes normally
- **THEN** native TTY streams pass through unchanged via the captured foreground process step
- **AND** no Rich live wrapper or bounded document intercepts child streams

#### Scenario: Foreground run dry-run

- **WHEN** `odcli run --dry-run` executes with the default format, `--format rich|json|toon`, or `--json`
- **THEN** no child process starts and the plan is emitted through the bounded output adapter in the selected format
- **AND** `--json` produces the same JSON document as `--format json`

#### Scenario: Interactive shell dry-run

- **WHEN** a spawning `odcli shell --dry-run` executes with the default format, `--format rich|json|toon`, or `--json`
- **THEN** no shell child starts and its captured command plan is emitted as one bounded document in the selected format
- **AND** `--json` produces the same JSON document as `--format json`
- **AND** normal `odcli shell` retains native interactive transport

#### Scenario: Raw-stream output option lacks dry-run

- **WHEN** `odcli run` or spawning `odcli shell` receives `--format rich|json|toon` or `--json` without `--dry-run`
- **THEN** Click exits `2` with a usage error
- **AND** SDK resolution, command construction, and child-process launch do not occur

## MODIFIED Requirements

### Requirement: Stable machine output

The exact bounded structured leaf inventory for normal execution SHALL be the canonical `tests/unit/test_cli_output_modes.py::PUBLIC_LEAF_CASES` data, currently: `init`, `doctor`, `env checkout`, `env list`, `env remove`, `env sync`, `db refresh`, `db reset-admin-password`, `eval`, `exec`, `test`, `module list`, `module update`, `module test`, `translations export`, `deps verify`, `vscode generate`, `postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`. The spec and its tests SHALL derive/verify this one canonical inventory rather than maintain a second table. Each bounded normal-execution leaf SHALL accept command-local `--format rich|json|toon`; `rich` SHALL be the default. Existing `--json` SHALL remain a backward-compatible alias for `--format json`. Supplying `--json` with `--format toon` or `--format rich` SHALL be a Click usage error with exit code `2`; supplying `--json --format json` SHALL be accepted. During normal execution, `run`, interactive `shell`, and `logs --follow` SHALL remain raw-streaming and SHALL not emit a document or use a Rich live wrapper. Eligible spawning `run` and `shell` SHALL accept document-format options only together with `--dry-run`; dry-run suppresses native execution and emits one bounded plan document in Rich, JSON, or TOON, with `--json` equivalent to `--format json`.

The CLI SHALL define one CLI-only `OutputMode` with values `rich`, `json`, and `toon`. The mode and envelope types SHALL NOT become public SDK models or FastAPI response models. Each successful or failed bounded operation, including bounded dry-run for an otherwise native-stream command, SHALL first build one JSON-safe CLI envelope v1 containing `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, and `warnings`; success SHALL contain equal `result` and `data`, and failure SHALL contain stable `error.code` and sanitized `error.message`.

JSON and TOON SHALL serialize that exact envelope without building format-specific result graphs. Decoding a TOON document with the selected strict decoder SHALL yield the same JSON value as decoding JSON output for the same operation. Machine modes SHALL emit exactly one UTF-8 document to stdout with no ANSI, prompt, status, progress, or external log text; diagnostics SHALL go to stderr. Renderer selection SHALL NOT change operation execution, exception mapping, or exit code. Native Click parse failures that occur before output-mode resolution SHALL retain Click's stderr usage output and exit code `2`.

For `env remove`, JSON and TOON document modes (including the `--json` alias) SHALL never call `click.confirm`. Without `--yes`, they SHALL NOT execute removal and SHALL emit exactly one sanitized failure envelope with `error.code="confirmation_required"` and exit code `1`. With `--yes`, JSON and TOON SHALL execute the same removal operation and normal success/failure mapping. Interactive Rich mode SHALL retain its existing confirmation behavior.

Rich renderers SHALL remain adjacent to the concrete commands whose typed results they render. They MAY use `Table`, `Status`, `Progress`, and `Live` only when appropriate to the operation; they SHALL NOT introduce a generic renderer interface, registry, or DSL.

#### Scenario: Native command dry-run supports every bounded format

- **WHEN** `odcli run --dry-run` or spawning `odcli shell --dry-run` is requested with `--format rich|json|toon` or `--json`
- **THEN** output contains exactly one bounded plan with `dry_run=true` in the selected format
- **AND** `--json` and `--format json` produce equivalent JSON documents
- **AND** no native child stream starts

#### Scenario: Normal native command stays raw

- **WHEN** `odcli run` or interactive `odcli shell` executes without `--dry-run`
- **THEN** its inherited stream is not wrapped in a bounded document or Rich live view

#### Scenario: Normal native command rejects machine options

- **WHEN** `odcli run` or spawning `odcli shell` is invoked with `--format` or `--json` but without `--dry-run`
- **THEN** Click exits `2` before invoking SDK code or starting a process

#### Scenario: Canonical bounded inventory remains single-source

- **WHEN** the stable machine-output characterization gate compares the documented normal-execution leaves
- **THEN** they equal canonical `PUBLIC_LEAF_CASES`, including `test`, `db refresh`, and `db reset-admin-password`
- **AND** no second bounded-leaf table is introduced

#### Scenario: JSON envelope

- **WHEN** `odcli env list --json` executes
- **THEN** stdout contains exactly one versioned envelope and no progress or log text

#### Scenario: JSON alias preserves envelope v1

- **WHEN** `odcli env list --json` and `odcli env list --format json` run against the same frozen result
- **THEN** each stdout document decodes to the same envelope v1 and contains no ANSI or diagnostic text

#### Scenario: TOON is semantically equal to JSON

- **WHEN** a bounded command succeeds or fails once and its envelope is emitted as JSON and TOON
- **THEN** strict TOON decoding and JSON decoding produce equal Python builtins including `result`/`data`, context, provenance, warnings, and error fields

#### Scenario: Conflicting alias is usage error

- **WHEN** a caller supplies `--json --format toon`
- **THEN** Click exits `2`, does not execute the operation, and does not emit a partial machine document

#### Scenario: Machine diagnostics stay on stderr

- **WHEN** a bounded machine-mode operation reports a sanitized diagnostic in addition to its result
- **THEN** stdout contains exactly one JSON or TOON envelope and the diagnostic is written only to stderr

#### Scenario: Machine remove requires explicit confirmation

- **WHEN** `odcli env remove ENV --format json`, `--format toon`, or `--json` is invoked without `--yes`
- **THEN** no prompt is rendered, removal is not called, stdout contains one failure envelope with `error.code="confirmation_required"`, and the command exits `1`

#### Scenario: Explicit machine remove executes

- **WHEN** `odcli env remove ENV --yes --format json`, `--format toon`, or `--json` is invoked
- **THEN** the same removal operation runs once and its result is emitted as one document under the normal renderer-independent exit mapping

#### Scenario: Secrets redacted

- **WHEN** error occurs during checkout
- **THEN** every machine or Rich error message redacts passwords, config body, and environment variables before emission

## ADDED Requirements

### Requirement: Shared confirmation ordering

For commands requiring confirmation, plan construction SHALL occur before confirmation. Machine modes and dry-run SHALL never prompt; dry-run SHALL stop after valid plan emission, while normal machine mode SHALL retain each command's existing explicit-confirmation requirement.

#### Scenario: Dry-run of destructive command

- **WHEN** a destructive command is invoked with `--dry-run` and without its apply/yes flag
- **THEN** the valid plan is emitted without prompting or mutating

#### Scenario: Normal machine removal lacks confirmation

- **WHEN** `env remove` runs in JSON or TOON mode without `--yes` and without `--dry-run`
- **THEN** it retains the existing `confirmation_required` failure and performs no removal
