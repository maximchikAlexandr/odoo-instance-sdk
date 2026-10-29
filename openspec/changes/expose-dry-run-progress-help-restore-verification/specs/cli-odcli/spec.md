## ADDED Requirements

### Requirement: Machine-readable help comes from the executable command graph

Every registered command and group SHALL support help projection in JSON and TOON from the same live Click objects used by Rich help. Root help SHALL expose the registered command tree; descendant help SHALL expose the selected subtree. The versioned projection SHALL include command path, description, arguments, options, parameter type, required state, safe default, repeatability, choices/ranges when present, and child commands. It SHALL NOT use a second runtime registry, `PUBLIC_LEAF_CASES` as production metadata, or evaluation of callable, environment-derived, or secret defaults.

#### Scenario: Root JSON help
- **WHEN** `odcli --help --format json` is invoked
- **THEN** stdout contains one versioned JSON help tree matching the registered Click root and exits zero without project resolution or domain execution

#### Scenario: Leaf TOON help
- **WHEN** `odcli git clone --format toon --help` is invoked
- **THEN** strict TOON decoding yields the same selected command metadata as JSON and no Rich table or ANSI appears

#### Scenario: Required and default metadata
- **WHEN** an argument or option has a Click type, required flag, choice/range, repeatability, or static public default
- **THEN** machine help exposes those values with their native JSON-safe types and the ordinary help description comes from the same Click parameter

#### Scenario: Unsafe default is not evaluated
- **WHEN** a parameter default is callable, environment-derived, or marked sensitive
- **THEN** help marks the default unavailable without evaluating or revealing it

#### Scenario: Rich help remains default
- **WHEN** help is requested without a machine format
- **THEN** the existing Rich Click help and usage-error behavior remains unchanged

### Requirement: Dry-run presents structured filesystem consequences consistently

Rich dry-run SHALL render the ordered structured file effects from each `ActionStep`, including action, path, move destination, condition, and uncertainty. JSON and TOON SHALL preserve the same values without parsing human descriptions. The bounded command inventory and raw-stream exclusions SHALL remain governed by the existing stable machine-output contract and test-owned `PUBLIC_LEAF_CASES`.

#### Scenario: Rich and machine plans agree
- **WHEN** the same frozen command is previewed in Rich, JSON, and TOON
- **THEN** Rich presents every structured effect in order and decoded JSON/TOON contain equal effect values and existing plan fields

#### Scenario: Inapplicable effect is absent
- **WHEN** the selected mode preserves a file or does not request a write
- **THEN** no format presents that mutation as unconditional

#### Scenario: Dry-run is inert
- **WHEN** any affected command is invoked with `--dry-run`
- **THEN** read-only planning may occur but no export, write, delete, restore, Git-history mutation, process-owned publication, or target-state mutation occurs

### Requirement: Machine progress and final documents use separate streams

Normal JSON and TOON execution of clone, backup download, and restore SHALL emit only the final output envelope on stdout and SHALL emit live versioned progress frames only on stderr. JSON progress SHALL use one compact JSON object per line. TOON progress SHALL use ASCII Record Separator before each complete TOON document and LF after it. Rich progress and normal diagnostics SHALL retain their existing behavior; dry-run SHALL not emit progress.

#### Scenario: Final stdout remains parseable
- **WHEN** a machine-mode long operation emits any number of progress events and completes
- **THEN** parsing stdout once yields exactly the existing final envelope and progress is available only from the documented stderr framing

#### Scenario: Unknown transfer length
- **WHEN** total download bytes are unavailable
- **THEN** Rich and machine progress show downloaded bytes and measured speed without a percentage or invented total

#### Scenario: Secret-bearing diagnostics
- **WHEN** a progress callback or failure contains a token, password, secret path component, or file content
- **THEN** the emitted event/diagnostic is sanitized before framing and the final document remains redacted
