# Spec Delta

## ADDED Requirements

### Requirement: Core artifacts exclude the Codex extension
Building the root project SHALL still produce one core wheel and one core sdist. Those artifacts SHALL NOT contain `odcli_codex` or require `openai-codex`. A separate build of `odcli-codex` with source overrides disabled SHALL produce that member's wheel and sdist. Its metadata SHALL name `odcli-codex`, version `0.1.0`, the `odcli-codex` script, and a version range on `odoo-instance-sdk`. CI SHALL prove a core-only install and a core-plus-extension install. The repository SHALL NOT lock the extension to the same release as core; the package-qualified tag form is `odcli-codex-v0.1.0`.

#### Scenario: Root build stays a single core distribution
- **WHEN** `uv build` runs at the repository root
- **THEN** `dist/` contains one core wheel and one core sdist
- **AND** neither artifact's metadata depends on `openai-codex`

#### Scenario: Member build does not use the workspace path
- **WHEN** `odcli-codex` is built with source overrides disabled
- **THEN** its requires-dist entry is a version range on `odoo-instance-sdk`
- **AND** the metadata does not point at the workspace checkout
