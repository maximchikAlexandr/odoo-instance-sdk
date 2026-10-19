## MODIFIED Requirements

### Requirement: Hot-fix identity remains isolated without checkout policy

The existing `odcli-fix-ISSUE` workflow SHALL retain its positive issue identity, exact reviewed SHA, PR linkage, isolated `~/.odcli-fix-ISSUE`, shared lifecycle, and merge/ancestry/issue/branch retirement gates. A hot-fix source SHALL be accepted in exactly one of these forms: (1) the canonical direct URL `https://github.com/maximchikAlexandr/odoo-instance-sdk.git` together with a separate `vcs_info.commit_id` containing the exact reviewed lowercase 40-character SHA, or (2) the pinned URL `git+https://github.com/maximchikAlexandr/odoo-instance-sdk.git@<same-sha>` together with that same `vcs_info.commit_id`. Foreign repositories, mismatched SHAs, uppercase or non-40-character IDs, URL query/fragment/suffix variants, and unpinned sources SHALL fail before coordinator invocation. The workflow SHALL NOT allocate or manage numbered checkouts, ports, databases, or filestores.

#### Scenario: Matching VCS pin is accepted

- **WHEN** the hot-fix source is `git+https://github.com/maximchikAlexandr/odoo-instance-sdk.git@<reviewed-sha>` and `vcs_info.commit_id` is the same exact lowercase SHA
- **THEN** provenance validation succeeds and the shared coordinator receives the exact reviewed source

#### Scenario: Canonical direct URL uses separate VCS identity

- **WHEN** the hot-fix source is `https://github.com/maximchikAlexandr/odoo-instance-sdk.git` and `vcs_info.commit_id` is the exact reviewed lowercase SHA
- **THEN** provenance validation succeeds without requiring an `@SHA` suffix in the URL

#### Scenario: Mismatched VCS pin is rejected

- **WHEN** the repository URL is foreign, the SHA differs from `vcs_info.commit_id`, the ID is not a lowercase 40-character exact SHA, or the URL has a query, fragment, or suffix
- **THEN** validation fails before coordinator invocation and no hot-fix identity is published

#### Scenario: Hot-fix creation remains thin
- **WHEN** a reviewed hot-fix revision is installed
- **THEN** the skill applies GitHub/reviewer policy and delegates lifecycle mechanics to the shared engine without invoking numbered checkout allocation
