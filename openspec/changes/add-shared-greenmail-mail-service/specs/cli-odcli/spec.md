## ADDED Requirements

### Requirement: Mail test commands delegate to the SDK

The Click group `odcli mail` MUST register `up`, `status`, `down`, and `configure`. `configure` MUST accept `--undo` and `--dry-run`. Every leaf MUST call the matching public SDK command (`mail_up_command`, `mail_status_command`, `mail_down_command`, or `mail_configure_command`) and MUST emit that command through the shared output pipeline. The callbacks MUST NOT construct Docker arguments, Odoo shell source, or mailbox secrets themselves. Machine-readable output MUST be `--format json`.

#### Scenario: Configure selects the current OdCLI context

- **WHEN** `odcli mail configure` runs without `--project` or `--env` from a project checkout
- **THEN** the callback passes the instance resolved by the normal OdCLI context rules to `mail_configure_command`
