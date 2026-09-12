## ADDED Requirements

### Requirement: Project ownership captured at download start
Project-backed backup operations SHALL pass the resolved canonical project identifier into `start_download` for local and remote sources; source type SHALL NOT determine project ownership. [Source: GH#64 §1]

#### Scenario: Start project download
- **WHEN** backup download is planned and executed from a resolved project owner
- **THEN** its catalogue row records that project before transfer state advances
