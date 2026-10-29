## ADDED Requirements

### Requirement: Focused environment inspection resolves before collection

Focused environment inspection SHALL resolve an explicit name or UUID, or the registered environment containing cwd, from the existing local catalogue before any runtime, process, Docker, Git, storage, database, or network probe begins. A missing or ambiguous selector SHALL fail with the existing sanitized diagnostic before expensive probes. For a selected environment, the command SHALL request exactly one canonical `EnvironmentMonitor.snapshot(project_id=selected_project_id)` and SHALL select the environment, project, and cluster from that single snapshot. It SHALL NOT add a cache, daemon, second monitor, or alternate collector.

#### Scenario: Unknown selector avoids probes
- **WHEN** focused inspection receives an environment selector absent from the local catalogue
- **THEN** it reports the existing not-found diagnostic and starts no snapshot or expensive probe

#### Scenario: Ambiguous selector avoids probes
- **WHEN** focused inspection receives a name matching more than one environment
- **THEN** it reports the existing ambiguity diagnostic with stable identities and starts no snapshot or expensive probe

#### Scenario: Selected project bounds collection
- **WHEN** focused inspection resolves a known name, UUID, or registered cwd environment
- **THEN** it requests one snapshot for that environment's project, selects from that sample, and performs no probes for other projects

#### Scenario: Focused result retains compatibility
- **WHEN** the selected environment is stopped or has unavailable metrics
- **THEN** the focused result retains its current typed fields, provenance, stopped state, explicit unavailable values, and machine/human rendering
