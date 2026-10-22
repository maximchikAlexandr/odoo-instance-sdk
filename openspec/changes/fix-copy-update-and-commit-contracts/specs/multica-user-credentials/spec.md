## ADDED Requirements

### Requirement: Project-aware automation commits as the verified root human creator

Multica commit automation SHALL obtain the selected member UUID from the already verified `client.context()` and `root_creator()` lineage before invoking project-aware `git commit`. It SHALL require a finite same-workspace parent chain, compatible project identity, and a human root creator, then pass that Multica member UUID to the core project-aware commit command for host mapping and GitLab token-owner verification. It SHALL NOT use issue metadata, child creator, assignee, current agent, workspace owner, GitLab numeric user ID, or machine account as a substitute. The resolved lineage and verified Git identity SHALL be revalidated before commit execution.

#### Scenario: Child work package commits as root creator

- **WHEN** verified task context belongs to a child issue whose valid lineage ends at a human-created planning root
- **THEN** automation passes that root creator's Multica member UUID to project-aware commit and the resulting commit uses the corresponding verified GitLab name and email

#### Scenario: Root creator is not verified

- **WHEN** lineage is missing, cyclic, cross-workspace, project-incompatible, or ends at a non-human creator
- **THEN** automation fails before credential lookup or `git commit` and does not substitute an assignee, agent, metadata value, or machine identity

#### Scenario: Identity changes after preview

- **WHEN** root lineage or the selected host-scoped GitLab identity differs between preview and execution revalidation
- **THEN** execution fails before creating a commit and does not rebuild the snapshot with the changed identity
