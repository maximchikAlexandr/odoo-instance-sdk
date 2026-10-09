## ADDED Requirements

### Requirement: Ephemeral host-scoped child credentials
Secret-bearing Git and GitLab execution inputs SHALL be captured privately at command construction, associated with one exact normalized HTTPS host, and supplied only to the intended child process or HTTP request. Public projections SHALL show the credential source and host but SHALL redact token values, authorization headers, credential-helper environment, stdin, scripts, observations, errors, and fingerprints. Parallel commands for different users SHALL have independent immutable private snapshots and SHALL NOT mutate global or repository Git credential configuration.

#### Scenario: Two users run Git concurrently
- **WHEN** two commands for different root creators access the same GitLab host concurrently
- **THEN** each child receives only its own credential snapshot and neither command changes the other's environment or Git configuration

#### Scenario: Plan and child output contain a token
- **WHEN** credential material occurs in a private environment value or is echoed by a child
- **THEN** the public plan, bounded result, logs, exception graph, and fingerprint contain no token bytes

### Requirement: Publication effects use existing execution boundaries
Caddy validation/reload and native Git passthrough SHALL use `internal/proc`; GitLab HTTP and replacement of the single owned Caddy route file SHALL use the existing action-step convention. Convenience methods SHALL delegate to their `*_command()` siblings, and dry-run SHALL execute neither processes, HTTP requests, nor filesystem publication.

#### Scenario: Inspect a publication plan
- **WHEN** a caller constructs publish or MR commands
- **THEN** every possible process and effect appears in execution order with stable step identifiers before mutation
