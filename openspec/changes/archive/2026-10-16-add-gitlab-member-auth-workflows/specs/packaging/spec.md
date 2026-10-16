## ADDED Requirements

### Requirement: Update regression uses distributable dashboard artifacts

The packaging suite SHALL build the React UI through the existing package workflow, produce wheel/sdist with required SPA assets, install a supported dashboard-enabled uv tool from Git provenance, execute the normal update path to an immutable revision, and verify the restarted installed monitor/API. The regression SHALL NOT use a manual post-update `uv tool install`, source-tree web assets, or undeclared Node.js runtime dependency.

#### Scenario: Packaged update retains monitor UI runtime
- **WHEN** the isolated dashboard update regression runs against built distribution inputs and the target revision
- **THEN** the updated tool contains the built SPA entry assets and dashboard Python dependencies and serves its monitor API after restart
