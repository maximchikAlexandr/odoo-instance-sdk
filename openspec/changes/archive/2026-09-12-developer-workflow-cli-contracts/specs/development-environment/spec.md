## ADDED Requirements

### Requirement: Tracker-neutral ticket allocation
Environment checkout planning SHALL model ticket allocation with neutral `Ticket` names while preserving first unsuffixed branch, subsequent `_N`, maximum evidence across local/catalogue/origin, non-reuse of historical iterations, and stale-collision failure. [Source: GH#64 §11]

#### Scenario: Allocate without tracker integration
- **WHEN** a valid ticket key is supplied
- **THEN** allocation uses only Git refs and catalogue evidence and never verifies the ticket through an external API

### Requirement: Unified environment storage location
New global environment worktrees SHALL live below the unified `~/.odcli/` root while repository-local `<project>/.odcli/` remains unchanged. [Source: GH#64 §7]

#### Scenario: Create environment after migration
- **WHEN** checkout provisions a new environment
- **THEN** every SDK-owned global artifact is rooted below the canonical user root
