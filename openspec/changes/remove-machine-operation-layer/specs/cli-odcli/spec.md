## REMOVED Requirements

### Requirement: CLI paths bind to stable operation IDs
**Reason**: Stable operation IDs and the production binding inventory are removed.
**Migration**: Friendly Click paths remain stable and the test-owned CLI matrix checks path completeness and SDK delegation.

### Requirement: Operation contract commands are read-only
**Reason**: Contract export and discovery commands are removed.
**Migration**: No replacement command is provided; callers use direct SDK/CLI/OpenAPI contracts.

### Requirement: Local operation invoke preserves output boundary
**Reason**: Local invoke and approval-session transports are removed.
**Migration**: Existing bounded friendly commands retain their current output boundary and confirmation behavior.

### Requirement: Native and streaming CLI transports remain native
**Reason**: This requirement existed only to contrast native paths with the removed finite operation invoke.
**Migration**: Existing native, streaming, Rich-live, and interactive command requirements continue unchanged in their owning capabilities.
