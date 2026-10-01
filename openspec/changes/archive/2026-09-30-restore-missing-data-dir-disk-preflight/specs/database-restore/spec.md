## ADDED Requirements

### Requirement: Restore disk preflight measures a valid containing filesystem

Restore command construction SHALL determine the inspection directory from the configured restore `data_dir`. When the configured destination does not exist, it SHALL select the nearest existing directory ancestor without creating the destination or any missing parent. When the configured destination already exists as a directory, it SHALL inspect that directory. When no `data_dir` is configured, the existing backup-directory fallback SHALL remain in effect.

After selecting an inspection directory, restore preflight SHALL measure its filesystem and SHALL retain the existing reserve calculation of the greater of 1 GiB or 10 percent of measured free space. Archive size, operator-limit, CRC, and other restore safety checks SHALL remain unchanged.

#### Scenario: Missing nested data directory has sufficient capacity

- **WHEN** a restore dry-run targets a missing nested `data_dir`, its nearest existing directory ancestor is inspectable, and the measured filesystem satisfies the existing reserve policy
- **THEN** public restore command construction succeeds
- **AND** the missing `data_dir` and its missing parents remain absent

#### Scenario: Existing data directory has sufficient capacity

- **WHEN** a restore dry-run targets an existing directory whose measured filesystem satisfies the existing reserve policy
- **THEN** public restore command construction succeeds using that directory for capacity inspection

#### Scenario: Measured capacity is insufficient

- **WHEN** the selected existing inspection directory has less usable free space than the archive requires after applying the existing reserve policy
- **THEN** restore planning fails with the existing typed insufficient-disk failure and reports the measured capacity and reserve

#### Scenario: No configured data directory

- **WHEN** restore planning has no configured `data_dir`
- **THEN** the existing backup-directory fallback is used for filesystem measurement

### Requirement: Restore disk inspection failures are truthful and actionable

Restore disk preflight SHALL treat only a missing path component as a reason to continue searching toward the parent. It SHALL fail closed when path resolution or traversal fails, when the nearest existing entry is not a directory, or when filesystem capacity cannot be inspected. Such a failure SHALL use a dedicated typed backup-policy inspection error with a stable error code and sanitized details identifying the requested path, the attempted inspection path when available, and the operating-system reason.

An unmeasured inspection failure MUST NOT be classified as insufficient disk space, MUST NOT fabricate zero available bytes or zero reserve bytes, and MUST NOT bypass archive capacity protection.

#### Scenario: Existing path component is not a directory

- **WHEN** a configured missing destination is nested under an existing regular file or another non-directory entry
- **THEN** restore planning fails with the typed disk-inspection error and identifies the invalid path component
- **AND** it does not report measured insufficient capacity

#### Scenario: Filesystem inspection raises an operating-system error

- **WHEN** resolving, traversing, or measuring the selected restore path raises a permission or I/O error
- **THEN** restore planning fails with the typed disk-inspection error and actionable sanitized path diagnostics
- **AND** no directory is created

#### Scenario: Public machine-readable dry-run reports inspection failure once

- **WHEN** public `db restore --dry-run` command construction cannot inspect the filesystem for the configured `data_dir`
- **THEN** the CLI emits one machine-readable failure envelope whose message identifies disk/path inspection rather than insufficient measured capacity
- **AND** no Odoo or PostgreSQL service is started or contacted
