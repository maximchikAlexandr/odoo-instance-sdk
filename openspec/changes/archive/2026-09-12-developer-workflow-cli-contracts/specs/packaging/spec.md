## ADDED Requirements

### Requirement: Optional host tools are not package dependencies
Neither `msgfmt` nor `git-absorb`, any wrapper/downloader/installer, or an `odcli-absorb` package SHALL appear in core or optional Python dependency metadata. Commands SHALL discover these host executables on demand and doctor SHALL report optional capability without making unrelated health fail. [Sources: GH#54; GH#65]

#### Scenario: Build package metadata
- **WHEN** wheel, sdist, and dependency inventories are checked
- **THEN** no gettext or git-absorb package boundary has been introduced
