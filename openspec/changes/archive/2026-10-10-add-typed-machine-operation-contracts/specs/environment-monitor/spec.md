## ADDED Requirements

### Requirement: Finite snapshot section selection

`EnvironmentMonitor.snapshot()` and its command form SHALL accept a frozen typed selection of catalogue, runtime, Git, storage, artifact, PostgreSQL and Docker-backed sections. Catalogue/project/environment selection SHALL complete before expensive probes. An unselected section SHALL start no probe, perform no discovery for that section and populate no section cache.

#### Scenario: Git and storage are unselected

- **WHEN** a finite request selects only catalogue and runtime lifecycle sections
- **THEN** Git, filesystem-size, Docker stats and PostgreSQL probes are not invoked

#### Scenario: Batch uses one selection pass

- **WHEN** one request selects multiple projects/environments and sections
- **THEN** the monitor plans catalogue rows once, applies filters before probes and shares one observation boundary across the batch

### Requirement: Snapshot v3 observation truth

Snapshot schema version 3 SHALL preserve existing version-2 fields and add a shared UTC observation time plus requested, completed and unknown section metadata. Each selected section SHALL report observed time, optional source age, completeness and a bounded sanitized reason when partial or unknown. Missing or unavailable data SHALL NOT be encoded as fresh zero/false values.

#### Scenario: One probe is unavailable

- **WHEN** Docker stats fail while catalogue and runtime collection succeed
- **THEN** the snapshot retains the successful sections, marks Docker incomplete/unknown with a sanitized reason and uses one shared observation time

#### Scenario: Unrequested differs from unknown

- **WHEN** storage is not requested and Git is requested but unavailable
- **THEN** storage appears only in the requested-section metadata as unrequested while Git is explicitly unknown/incomplete

### Requirement: Honest process CPU samples

Every observed process group SHALL publish confirmed process identity, raw cumulative CPU seconds and sample time when available. CPU percentage SHALL be present only when calculated from two compatible samples for the same PID/create-time identity held by the same monitor or explicitly supplied by the caller. A fresh one-shot process SHALL return raw counters and unknown percentage rather than treating a first sample as zero or reusing another process history.

#### Scenario: First finite sample has raw counter

- **WHEN** a new CLI process takes its first process snapshot
- **THEN** it returns process identity, cumulative CPU seconds and sample time, with CPU percentage unknown

#### Scenario: Compatible previous sample yields percentage

- **WHEN** the same monitor has a previous sample with matching PID/create-time and increasing sample time
- **THEN** it derives percentage from the counter delta and elapsed time

#### Scenario: PID reuse invalidates history

- **WHEN** PID matches but create-time differs
- **THEN** previous history is ignored and percentage remains unknown

### Requirement: Existing resource safety semantics remain intact

Section selection and CPU changes SHALL preserve Darwin physical-footprint semantics, non-Darwin RSS semantics, recursive process-tree ownership, shared-PID deduplication, project/environment attribution and the real production Docker collector path. They SHALL NOT add a global cache or second monitor graph.

#### Scenario: Shared PID remains counted once

- **WHEN** selected sections observe the same confirmed process identity from more than one owning row
- **THEN** the existing ownership/deduplication rules attribute it once and section selection does not duplicate CPU or memory
