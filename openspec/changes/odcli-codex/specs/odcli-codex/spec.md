# Spec Delta

## Purpose

Provide an optional Codex-backed distribution that fills missing Odoo translation entries and returns one English commit description without adding an AI runtime to core `odcli`.

## ADDED Requirements

### Requirement: Independent odcli-codex distribution
The repository SHALL publish `odcli-codex` as its own distribution, import package, and `odcli-codex` console script inside the existing uv workspace. The root project SHALL remain `odoo-instance-sdk` at `src/odoo_instance_sdk`. Published metadata SHALL depend on `odoo-instance-sdk` with a compatible version range and SHALL remain valid when built with source overrides disabled. The extension SHALL NOT import `odoo_instance_sdk.internal`. Core runtime dependencies SHALL NOT include this distribution or `openai-codex`.

#### Scenario: Core-only install has no extension
- **WHEN** a clean environment installs only the built core wheel
- **THEN** `odcli` is available and importing `odcli_codex` fails
- **AND** the core wheel metadata does not require `openai-codex` or `odcli-codex`

#### Scenario: Extension installs beside core
- **WHEN** a clean environment installs the built core wheel and the built `odcli-codex` wheel
- **THEN** both `odcli --help` and `odcli-codex --help` exit 0
- **AND** `odcli-codex` help lists `translations fill` and `git message`

#### Scenario: Extension-only tool install hides core executables
- **WHEN** a user installs only the `odcli-codex` tool distribution
- **THEN** `odcli-codex` is on `PATH`
- **AND** the core `odcli` executable is not published by that install

### Requirement: Missing-entry translation fill
`odcli-codex translations fill PATH` SHALL update an existing Odoo `.po` file. It SHALL resolve the language from the catalog header and filename, and SHALL require `--language` only when those sources are missing or disagree. It SHALL fill only empty `msgstr` values, including empty plural forms. It SHALL leave headers, comments, fuzzy entries, existing translations, order, source references, and plural structure unchanged. It SHALL reject a proposed string whose placeholders, interpolation keys, markup, escapes, or plural shape differ from the source entry, report that entry, and leave it untranslated. It SHALL write the file atomically only after the whole catalog parses and optional `msgfmt` validation passes. The result SHALL include translated, fuzzy, and untranslated counts and the bounded `msgfmt` output when that tool ran. `--dry-run` SHALL describe the file, language, model, context manifest, and planned request and SHALL NOT call Codex or modify the file.

#### Scenario: Empty entries are filled and existing text stays
- **WHEN** a catalog has one empty `msgstr`, one translated entry, and one fuzzy entry
- **THEN** only the empty entry changes
- **AND** the fuzzy entry and the existing translation remain byte-for-byte identical aside from the filled entry

#### Scenario: A broken placeholder is not written
- **WHEN** the model reply drops a named interpolation key or an XML tag present in the source
- **THEN** that entry stays empty
- **AND** the result reports the rejected entry
- **AND** the destination file is not replaced when the rest of the catalog cannot be validated

#### Scenario: Dry-run does not invent a translation
- **WHEN** fill is planned with `--dry-run`
- **THEN** the plan names the catalog, language, model, and context paths
- **AND** no model call occurs and the catalog bytes are unchanged

### Requirement: Bounded project context
Repeatable `--context PATH` SHALL accept files and directories inside the resolved project. A directory SHALL contribute only Git-tracked, non-ignored, non-binary text files. The command SHALL show the selected paths and the byte limit before sending content. It SHALL fail instead of sending inputs that are outside the project, ignored, binary, larger than the configured byte budget, or secret-like. Secret-like inputs include `.env`, `credentials*`, `*.pem`, `*.key`, `id_rsa`, `*.p12`, and content containing `BEGIN PRIVATE KEY`, `AKIA`, `api_key=`, or `token=`.

#### Scenario: Context outside the project is rejected
- **WHEN** `--context` names a path that resolves outside the project root
- **THEN** the command fails before any model call and names the rejected path

#### Scenario: Oversized or secret-like context is not sent
- **WHEN** selected context exceeds `max_context_bytes` or matches a secret-like name or marker
- **THEN** the command fails and does not include that content in a model request

### Requirement: One-line commit description
`odcli-codex git message` SHALL return one English description line and SHALL NOT stage, commit, or choose an Odoo prefix, module, ticket, or final commit message. It SHALL reject blank, multiline, overlong (more than 100 characters), non-ASCII, or control-character model output. `--plain` SHALL write only that line to stdout. Rich, JSON, and TOON SHALL use the core output contract, and machine stdout SHALL be one document. `--dry-run` SHALL NOT call Codex.

#### Scenario: Plain output is a single line
- **WHEN** `odcli-codex git message --plain` succeeds
- **THEN** stdout is exactly the description plus a trailing newline
- **AND** no progress or extra document is written

#### Scenario: Unsafe model text is rejected
- **WHEN** the model returns a blank line, more than one line, more than 100 characters, a non-ASCII character, or a control character
- **THEN** the command fails and does not print that text as a description

### Requirement: Codex settings and authentication boundary
`[codex]` in the existing `.odcli/project.toml` SHALL accept `model` and `max_context_bytes` (default 262144). `--model` SHALL override the file. The effective model SHALL be accepted only when the installed Codex runtime lists it. The package SHALL NOT store API keys or copy Codex credentials, and SHALL NOT hardcode a model name. A missing model SHALL fail before a turn starts.

#### Scenario: CLI model overrides project config
- **WHEN** the project file sets one model and `--model` sets another listed model
- **THEN** the request uses the CLI model

#### Scenario: Unknown model fails closed
- **WHEN** the effective model is absent from the installed runtime catalog
- **THEN** the command fails before sending source or diff content
