## ADDED Requirements

### Requirement: Exact backup UUID resolution

The backup resource and catalogue SHALL resolve a backup only by its complete UUID for point operations. Unknown, malformed, ambiguous, row-number, filename-glob, and arbitrary-path identifiers SHALL NOT select a backup.

#### Scenario: Exact UUID exists

- **WHEN** a caller supplies the complete UUID of a catalogue row
- **THEN** the resource returns that exact backup record and its state without scanning filenames

#### Scenario: UUID is unknown

- **WHEN** a valid UUID has no catalogue row
- **THEN** the operation fails with a typed not-found result and mutates no file or audit row

### Requirement: State-aware deterministic backup queries

Backup queries SHALL expose the catalogue state and actual file presence as separate fields, SHALL support source and database filters, and SHALL sort deterministically by newest catalogue time then UUID. The default query SHALL include only available backups; an explicit all-states query SHALL also include downloading, failed, and deleted records. Large queries SHALL use a validated limit and opaque deterministic keyset cursor over a single read transaction.

#### Scenario: Missing file is not a deleted state

- **WHEN** an available catalogue row points to a file that is absent
- **THEN** the query reports `state=available` and `file_present=false` separately
- **AND** its catalogue byte count is not reported as currently occupied disk bytes

#### Scenario: All states requested

- **WHEN** a caller requests all states
- **THEN** available, downloading, failed, and deleted records appear in deterministic order without deleting history

#### Scenario: Next page is requested

- **WHEN** a caller supplies the cursor returned by a limited query
- **THEN** the next read starts strictly after the last `(time, UUID)` key and contains no duplicate from the prior page

### Requirement: Backup lifecycle exclusion

Download, restore, validation where identity must remain stable, and deletion SHALL coordinate through the existing operation-lock mechanism keyed by backup UUID plus catalogue state. A downloading backup or an available backup actively held for restore SHALL not be deleted.

#### Scenario: Delete races restore

- **WHEN** restore holds the exact backup lock and deletion is requested
- **THEN** deletion fails as busy before unlinking or recording `deleted`

#### Scenario: Delete targets downloading row

- **WHEN** the selected catalogue row is still downloading
- **THEN** deletion refuses without changing the `.part` file or audit state

### Requirement: Audit-preserving safe backup deletion by UUID

UUID deletion SHALL capture and display the exact selected file, recorded size, and known restore/environment relationships before confirmation. Under the backup lock it SHALL re-read state and identity, reject a changed target or a symlink/containment escape, unlink only the selected file, verify absence, and then append the existing deleted audit state. Restore links, UUID, and history SHALL remain. Already-deleted or already-absent files SHALL return explicit idempotent outcomes, and a filesystem failure SHALL not be recorded as deletion.

#### Scenario: Exact available backup is deleted

- **WHEN** the confirmed UUID still resolves to the captured contained regular file and it is not active
- **THEN** only that file is unlinked, absence is verified, and the catalogue records `deleted` while retaining history and restore links

#### Scenario: Path changes after preview

- **WHEN** the recorded path, file identity, containment, or symlink status differs at execution
- **THEN** deletion fails closed before unlinking and does not select a replacement by name

#### Scenario: File deletion fails

- **WHEN** the operating system refuses to remove the selected file
- **THEN** the command fails and the catalogue does not record the backup as deleted

#### Scenario: File is already absent

- **WHEN** the exact available record's file is absent and no conflicting active lifecycle exists
- **THEN** deletion returns an explicit idempotent missing-file outcome and records the auditable deleted state without claiming bytes were freed
