---
name: odcli-developer-work
description: Develop the OdCLI repository and manage private numbered installations without expanding the public CLI.
---

# OdCLI developer work

This skill is for agents developing the OdCLI repository and maintaining its
private numbered installations. It is not part of ordinary OdCLI use.

## Private numbered service

Use the repository-owned `scripts/odcli_agent_tools/numbered_tool.py` wrapper
for numbered `create`, `list`, `inspect`, and `remove` operations. The wrapper
validates the trusted canonical interpreter and repository provenance before it
invokes the private SDK coordinator. Provide a full lowercase SDK SHA for
creation and treat its bounded JSON as the complete result; secrets must never
be printed, returned, stored in a manifest, or committed.

The service selects the smallest wholly free positive number, links one
independent checkout and SDK-owned Python environment, and delegates
configuration, port, database, catalogue, environment, and cleanup lifecycle
to the existing SDK environment APIs. Do not repair, adopt, overwrite, or
delete orphaned or foreign numbered paths. Removal is selective and
fail-closed: dirty, unpushed, running, mismatched, escaped, or otherwise
unverified resources remain for retry.

## Repository development

Use the repository's tests, lint, typing, packaging, documentation, and
OpenSpec gates for changes. Keep executable helper ownership in
`scripts/odcli_agent_tools/`; skill directories contain instructions and
non-executable prompt material only. The ordinary user workflow remains in
`odcli-autonomous-work`, including triage and reviewed hot-fix unblocking.
