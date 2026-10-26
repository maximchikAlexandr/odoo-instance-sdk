## ADDED Requirements

### Requirement: Unified configuration and identity CLI guidance
CLI help and maintained documentation SHALL identify `<ODCLI_USER_ROOT>/.env` as the recommended user dotenv, state process > project > user precedence, identify the former credential dotenv as compatibility-only, and warn that both dotenv files are owner-only and secret. Every command that accepts `--member-id` SHALL explain that the value is the person's Multica account `user_id` returned by `multica workspace member list --output json`, not the membership-row `id`, agent ID, or squad ID; examples SHALL use a public placeholder such as `MEMBER_USER_ID`. Project-aware `git commit --member-id` guidance SHALL include registered native checkouts without a local manifest and SHALL retain `--tag`; MR publication guidance SHALL distinguish configured assignee from GitLab reviewer semantics and state explicit `--assignee` precedence. No help, preview, result, error, or documentation example SHALL include a real token, password, private hostname, personal email, or secret value.

#### Scenario: Help describes unified user dotenv
- **WHEN** relevant database, GitLab credential, or MR publication help/documentation is rendered
- **THEN** it names the recommended user path and precedence without instructing users to duplicate secrets

#### Scenario: Help identifies the Multica member UUID field
- **WHEN** help or maintained documentation for a command accepting `--member-id` is rendered
- **THEN** it names `multica workspace member list --output json` as the lookup command and `user_id` as the required field
- **AND** it explicitly states that the membership-row `id`, agent ID, and squad ID are different identifiers and are not valid substitutes

#### Scenario: Native commit dry-run is safe
- **WHEN** project/environment selection resolves a registered native checkout and `git commit --member-id ... --tag ... --dry-run` succeeds
- **THEN** the bounded plan shows the policy subject plus safe verified author/committer name and email and contains no credential or ambient machine identity

#### Scenario: MR result reports assignee safely
- **WHEN** `odcli-multica gitlab mr publish` applies an explicit or configured assignee
- **THEN** Rich, JSON, and TOON output report the same ID, username, and selection source without calling that user a reviewer

