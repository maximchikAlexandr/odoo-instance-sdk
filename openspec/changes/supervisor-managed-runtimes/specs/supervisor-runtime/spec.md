# Spec Delta

## Purpose

Give OdCLI one private Supervisor that starts, watches, and stops Odoo runtimes and the dashboard server without adopting foreign processes.

## ADDED Requirements

### Requirement: Private user Supervisor

On Unix, OdCLI SHALL install Supervisor and SHALL use only a dedicated per-user `supervisord` under the OdCLI user root. Its configuration and Unix socket directory SHALL be owner-accessible only. OdCLI SHALL reach that daemon through `supervisorctl` and SHALL reconnect when the socket is already healthy. It SHALL NOT connect to a system Supervisor or control programs it did not register. When the daemon is not running, the next managed command SHALL start it. After a machine reboot the daemon SHALL stay stopped until the user runs a managed command again; programs SHALL NOT start merely because the machine booted.

#### Scenario: Missing daemon is started on demand

- **WHEN** a managed Unix command runs and the private Supervisor socket is absent
- **THEN** OdCLI starts its own daemon and uses that socket

#### Scenario: Existing private daemon is reused

- **WHEN** the private socket is already accepting connections
- **THEN** OdCLI reconnects and does not start a second daemon

#### Scenario: System Supervisor is ignored

- **WHEN** a system Supervisor is running
- **THEN** OdCLI does not query or change its programs

#### Scenario: Reboot does not resurrect programs

- **WHEN** the machine has rebooted and the user has not run a managed command
- **THEN** neither Odoo nor the monitor is started

### Requirement: Supervisor is unavailable on Windows

Managed `run`, `stop` of a Supervisor-owned runtime, and `monitor` SHALL fail on Windows with a clear message that Supervisor-managed lifecycle is unsupported. The message SHALL NOT claim the program was started or stopped.

#### Scenario: Windows managed run fails closed

- **WHEN** `odcli run` or `odcli run --detach` is invoked on Windows
- **THEN** the command fails before launching Odoo and explains that Supervisor-managed lifecycle is unsupported on Windows

### Requirement: One program per owned runtime

Each environment-owned Odoo, each project-owned Odoo, and the dashboard server SHALL have a stable Supervisor program name and its own program configuration. The program command SHALL be the captured executable, argv, cwd, and config. A second or overlapping start of the same program SHALL NOT create another process. `autostart` SHALL be false and `autorestart` SHALL be `unexpected`, with a finite startup retry limit. A manual stop SHALL leave the program stopped. An unexpected exit SHALL be restarted only while the daemon is running. Stop SHALL use a graceful signal, a bounded wait, and group stop for descendants that remain in the program's process group. OdCLI SHALL NOT claim it can reap descendants that left that process group.

#### Scenario: Repeated start does not duplicate

- **WHEN** the same runtime is started again while its program is already running
- **THEN** the existing program is reused and no second process is created

#### Scenario: Manual stop stays stopped

- **WHEN** the user stops a program and the process later remains exited
- **THEN** Supervisor does not start it again

#### Scenario: Unexpected exit restarts while the daemon lives

- **WHEN** a managed program exits unexpectedly and the private daemon is still running
- **THEN** Supervisor restarts that same program within the configured retry limit

### Requirement: Dashboard program is optional and independent

The dashboard program SHALL be registered only when the dashboard extra is installed and the user asks to start the monitor. Without that extra, managed Odoo SHALL still work, and an explicit monitor request SHALL fail with the existing install hint. Stopping, crashing, or restarting the monitor SHALL NOT stop Odoo. Stopping or restarting one Odoo SHALL NOT stop the monitor or any other runtime. The monitor command SHALL NOT include `stop all`, a global reload, or a restart of other programs.

#### Scenario: Core install does not register the monitor

- **WHEN** the dashboard extra is absent and the user starts Odoo
- **THEN** no monitor program is registered and Odoo still starts

#### Scenario: Monitor failure leaves Odoo running

- **WHEN** the monitor program exits or is stopped
- **THEN** every managed Odoo program keeps running

### Requirement: Saved monitor address

The first successful monitor start SHALL save its loopback host and port. Later starts and crash restarts SHALL reuse that address. If the saved port is occupied by a process that is not the monitor program, OdCLI SHALL fail with a clear error, SHALL NOT signal that process, and SHALL NOT silently choose another port. Changing the address SHALL happen only when the user explicitly requests a new host or port while the monitor is stopped.

#### Scenario: Crash returns to the same address

- **WHEN** the monitor exits unexpectedly and Supervisor restarts it
- **THEN** it listens on the saved host and port

#### Scenario: Occupied saved port fails closed

- **WHEN** the saved port is occupied by another process
- **THEN** start fails, the other process keeps running, and the saved address is unchanged

### Requirement: Secrets stay out of diagnostics

Supervisor configuration, socket, and logs SHALL be owner-readable only. Status, errors, and dry-run output SHALL NOT print passwords, secret argument values, or the raw program command when it contains secret material.

#### Scenario: Failure output redacts the program command

- **WHEN** a managed start fails and the program command contains a database password
- **THEN** the error names the failure state and does not include the password
