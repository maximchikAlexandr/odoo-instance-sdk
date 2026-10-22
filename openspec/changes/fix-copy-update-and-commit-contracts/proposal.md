## Why

COPY checkout can report an environment ready while Odoo restored file-backed attachments into a different filestore than the environment reads. The same release also leaves three user-facing consistency gaps: generated ticket links use obsolete angle brackets, `odcli update` can leave an installed companion pinned to an older SDK, and project-aware commits cannot apply a verified human GitLab identity without switching to generic repository mode.

## What Changes

- Bind the complete COPY backup/restore runtime, including an auxiliary Database Manager, to the captured target environment `data_dir`; verify restored file-backed attachments before checkout finalization while preserving source data, the primary runtime, backup ownership, and recovery behavior.
- Emit configured ticket URLs as ordinary second-paragraph URLs while continuing to validate existing angle-bracketed history and standard `Co-authored-by` trailers.
- Extend canonical `odcli update` composition discovery, immutable install planning, verification, and recovery to update an installed `odcli-multica` companion together with its SDK dependency; keep core-only installs core-only and never report aggregate success after a partial failure.
- Allow project-aware CLI/SDK commits to accept a verified Multica member UUID without `--repo`, resolve the root human creator through the existing verified Multica lineage, map that member to a verified GitLab identity, and capture safe author/committer fields in the same immutable commit snapshot.
- Add focused unit, component, packaging, and contract regressions for the four corrected behaviors. No live E2E run is part of this change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `development-environment`: COPY checkout and adoption restore into the target environment filestore and prove file-backed attachment integrity before `READY`.
- `odoo-git-workflow`: configured ticket links use bare URLs, and initialized-project commits can consume verified member identity without entering generic repository mode.
- `self-update`: canonical update coordinates an installed `odcli-multica` companion and its SDK dependency with the core installation lifecycle.
- `cli-odcli`: project-aware `git commit` accepts `--member-id` without `--repo` while preserving the existing project/environment selection and message policy.

## Impact

- Checkout execution, auxiliary restore configuration, COPY finalization checks, and their existing adoption/ordinary checkout tests.
- Odoo commit message construction, CLI validation, Git identity environment capture, history validation, and `odcli-multica` root-creator composition.
- Self-update provenance/composition models, frozen process steps, recovery snapshots/journal metadata, post-install launcher and revision verification, and packaging tests.
- No new public package, dependency, storage schema, process boundary, credential format, or generic credential-helper implementation.
