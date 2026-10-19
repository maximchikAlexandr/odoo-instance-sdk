## ADDED Requirements

### Requirement: Cross-project HTTP port allocation is bounded and deterministic

The shared HTTP allocator SHALL consider the inclusive range `8000` through `9000`. When a preferred project port is present and in range, it SHALL probe that port first. When no preferred port is present, it SHALL probe `8069` first. When the preferred port is outside the range, it SHALL ignore that value and begin the ascending automatic scan at `8000`; it SHALL not prioritize `8069` in that case. After any first probe, it SHALL scan every remaining candidate in ascending order through `9000` exactly once. It SHALL reject exhaustion after the full scan. An explicit HTTP port SHALL be probed exactly once even when outside the automatic range and SHALL not fall back to another port.

#### Scenario: Preferred HTTP port precedes the full scan

- **WHEN** automatic HTTP allocation has an in-range preferred port
- **THEN** that port is probed first, followed by each remaining candidate in ascending order without duplicate probes

#### Scenario: Absent preferred HTTP port defaults to 8069

- **WHEN** automatic HTTP allocation has no preferred port
- **THEN** port `8069` is probed first and the remaining `8000..9000` candidates are scanned once

#### Scenario: Out-of-range preferred HTTP port starts the ascending scan

- **WHEN** automatic HTTP allocation has a preferred port outside `8000..9000`
- **THEN** the preferred value is ignored and the scan starts at `8000` in ascending order, without giving `8069` special priority

#### Scenario: HTTP allocation reports exhaustion

- **WHEN** every candidate in `8000..9000` is occupied
- **THEN** allocation fails after probing the complete range and does not wrap or silently choose an outside port

#### Scenario: Explicit HTTP port is exact

- **WHEN** an explicit HTTP port is supplied, including one outside `8000..9000`
- **THEN** only that exact port is probed and no automatic fallback is attempted


### Requirement: Compose init keeps local administrator credentials private

Self-contained Compose initialization SHALL preserve a nonblank source or generated `admin_passwd`, generate a cryptographically random local value only when neither exists, and write it atomically to the owner-only `0600` generated configuration. The credential SHALL remain separate from the PostgreSQL master password and SHALL not appear in manifests, immutable plans, process arguments, logs, results, or exceptions.

#### Scenario: Existing source credential is preserved

- **WHEN** a source config contains a nonblank `admin_passwd`
- **THEN** generated Compose configuration preserves that value without changing the source file

#### Scenario: Missing credential is generated once

- **WHEN** source and generated configs do not contain a nonblank `admin_passwd`
- **THEN** init generates and writes a private local credential, and a later repair/re-init preserves it
