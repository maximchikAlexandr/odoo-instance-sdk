## REMOVED Requirements

### Requirement: Stable versioned operation identity
**Reason**: No current product consumer requires stable operation discovery or bindings.
**Migration**: Use existing Python SDK methods or friendly CLI paths directly.

### Requirement: Single production inventory
**Reason**: The production operation registry is removed with the machine layer.
**Migration**: Restore the exhaustive test-owned `PublicLeafCase` matrix for CLI characterization only.

### Requirement: Complete operation descriptor
**Reason**: Descriptor metadata exists only for the removed discovery/invoke surface.
**Migration**: Public SDK types and existing CLI help/output remain authoritative for their direct consumers.

### Requirement: Wire schemas follow actual DTO projection
**Reason**: No operation-contract bundle remains to export these schemas.
**Migration**: Existing command envelopes and OpenAPI schemas continue to use their current concrete DTO projections.

### Requirement: Deterministic contract bundle and consumer generation
**Reason**: Contract export and external/Go consumer generation are removed as unneeded scope.
**Migration**: Automation uses the direct SDK, current JSON CLI, or existing OpenAPI where applicable.

### Requirement: Local invocation is distinct from dispatch
**Reason**: `operation invoke` is removed and no daemon/dispatch replacement is added.
**Migration**: Invoke the existing SDK primitive or friendly CLI command directly.

### Requirement: One captured operation context
**Reason**: The operation-specific context has no remaining runtime consumer.
**Migration**: Existing CLI and SDK context resolution remains unchanged outside the removed layer.

### Requirement: Bounded local machine output
**Reason**: Operation document/session transports are removed.
**Migration**: Existing bounded CLI leaves retain Rich/JSON/TOON output and native leaves retain their direct transports.

### Requirement: Installed Python operation providers
**Reason**: Dynamic operation providers are unnecessary without discovery/export/invoke consumers.
**Migration**: Installed packages use existing public SDK extension points; any future operation plugin design requires a new change.

### Requirement: No plugin lifecycle framework
**Reason**: The entire operation-provider capability is removed, making its lifecycle constraint obsolete.
**Migration**: No replacement plugin lifecycle is introduced.

### Requirement: Representative end-to-end contract proof
**Reason**: The operation contract and its transport matrix no longer exist.
**Migration**: Existing targeted tests and required CI cover directly affected shared seams; no replacement live E2E is required for deletion-only graph revision 3.
