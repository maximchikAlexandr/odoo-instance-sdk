## ADDED Requirements

### Requirement: Public bounded Odoo inspector

`OdooInstance` SHALL expose one public `OdooInspector` as `instance.inspector`. It SHALL provide only model, field, and XML-ID convenience methods plus matching command methods returning immutable `Command[T]` values. Each convenience method SHALL construct its command sibling exactly once and return its result. The facade SHALL NOT expose generic ORM, SQL, record-value, or traversal operations.

#### Scenario: Convenience operation delegates once

- **WHEN** a caller invokes `instance.inspector.model("res.partner")`
- **THEN** the facade constructs `model_command("res.partner")` exactly once and returns its frozen `OdooModelInfo` result
- **AND** it does not independently rebuild the script, process arguments, transaction action, or result conversion

#### Scenario: Arbitrary inspection is unavailable

- **WHEN** the public inspector API and exports are inspected
- **THEN** they contain the three explicit operations and no generic `inspect`, query, search, read, write, SQL, or record-field operation

### Requirement: Frozen model and field metadata

The SDK SHALL return frozen, unknown-field-forbidden model and field metadata values. Model metadata SHALL contain identity, existence, bounded description/table, model flags, and field count. Field metadata SHALL contain identity, existence, bounded label/type/relation, field flags, and boolean related/computed presence. Strings SHALL be at most 256 code points; runtime objects, values, credentials, and configuration SHALL NOT be serialized.

#### Scenario: Existing model returns bounded metadata

- **WHEN** `model("res.partner")` runs against a configured Odoo 19 database containing that registry model
- **THEN** the result has `exists=true`, the exact technical name, bounded description and table, boolean model flags, and the non-negative count of registry fields
- **AND** the result contains no model object or record data

#### Scenario: Existing field returns bounded metadata

- **WHEN** `field("res.partner", "email")` runs and the field exists
- **THEN** the result has `exists=true`, exact model/name, type and label, the required/readonly/store/index flags, bounded relation when present, and only boolean related/compute presence
- **AND** it does not serialize the related path, compute callable, inverse/search callable, default, domain, or any record value

### Requirement: Deterministic missing registry results

A syntactically valid missing model or field SHALL return a successful typed result with `exists=false` and every metadata field that could only come from the missing object set to null. An invalid model or field identifier SHALL fail before command construction or child-process execution. Missing objects SHALL NOT be converted into an empty registry object, fabricated metadata, or an unsanitized traceback.

#### Scenario: Model is absent

- **WHEN** `model("x_missing.model")` runs and the registry contains no such model
- **THEN** it returns `exists=false` with nullable model metadata set to null and starts no second query or process

#### Scenario: Field is absent

- **WHEN** `field("res.partner", "x_missing_field")` runs and the model exists but the field does not
- **THEN** it returns `exists=false` with nullable field metadata set to null

#### Scenario: Invalid identifier is rejected early

- **WHEN** a model or field input contains whitespace, executable source, path syntax, or does not satisfy the documented technical-identifier grammar
- **THEN** validation fails before an Odoo command is constructed or executed

### Requirement: Bounded XML-ID resolution

`ref(xmlid)` SHALL use `env.ref(xmlid, raise_if_not_found=False)` and return a frozen, unknown-field-forbidden `OdooRefInfo` containing only `xmlid`, `exists`, nullable bounded `model`, nullable positive `record_id`, and nullable bounded `display_name`. A valid missing XML ID SHALL return `exists=false` with null record metadata. An invalid XML ID SHALL fail before execution. The operation SHALL NOT read or serialize any other record field.

#### Scenario: Existing reference returns identity only

- **WHEN** `ref("base.user_admin")` resolves successfully
- **THEN** the result contains the XML ID, `exists=true`, record model, positive record ID, and bounded display name
- **AND** it contains no login, password, token, group membership, or arbitrary field value

#### Scenario: Reference is absent

- **WHEN** `ref("base.x_missing_reference")` returns no record
- **THEN** the result contains the requested XML ID, `exists=false`, and null model, record ID, and display name

### Requirement: Read-only headless execution

Every inspector command SHALL reuse the existing configured headless Odoo-shell command with `commit=false`, the existing immutable process snapshot, `internal/proc/` execution, dependency readiness, redaction, and rollback action. It SHALL resolve the database from the existing `OdooInstance` and SHALL NOT start or require an HTTP server, commit a transaction, execute raw SQL, write a record, or create an alternative process boundary.

#### Scenario: Inspection rolls back without HTTP

- **WHEN** any inspector command executes against a ready project or environment while no Odoo HTTP server is running
- **THEN** one captured headless shell process returns the typed result and the command completes its rollback action
- **AND** no HTTP server, commit, database write, or second subprocess starts

#### Scenario: Plan and execution inputs match

- **WHEN** an inspector command is inspected and then run through a recording executor
- **THEN** the redacted runner input matches the immutable plan's Odoo argv, cwd, environment policy, fixed script, and rollback action
