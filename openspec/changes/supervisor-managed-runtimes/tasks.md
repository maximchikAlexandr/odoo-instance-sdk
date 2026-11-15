# Tasks

## 1. Supervisor home

- [x] 1.1 Add the Unix `supervisor` dependency and a private home under the OdCLI root with owner-only config and socket paths.
- [x] 1.2 Render daemon and program configuration, including `autostart=false`, `autorestart=unexpected`, group stop, and stable program names.
- [x] 1.3 Start or reuse the private daemon and run `supervisorctl` only through `internal/proc`.

## 2. Odoo lifecycle

- [x] 2.1 Put the Supervisor program, update, start, and status steps on the Unix `run` and `run --detach` snapshots without rebuilding argv at execution.
- [x] 2.2 Follow logs for foreground run, detach on Ctrl-C with exit 130, and keep the runtime row.
- [x] 2.3 Stop a registered program by name, treat a repeated stop as success, and keep the legacy signal path when no program exists.
- [x] 2.4 Fail Windows managed run and Supervisor-owned stop with the unsupported message.

## 3. Monitor

- [x] 3.1 Run Uvicorn from a Supervisor program that does not open a browser, and persist the chosen loopback address.
- [x] 3.2 Add `monitor` start, stop, restart, status, and logs. Open the browser only from the CLI after `/healthz`.
- [x] 3.3 Keep the dashboard-extra install error, refuse an occupied saved port, and leave Odoo running when the monitor stops.

## 4. Proof and docs

- [x] 4.1 Cover permissions, duplicate start, fatal start, manual stop, crash restart, monitor address, and Windows rejection with unit tests, plus one real temporary daemon.
- [x] 4.2 Update the CLI leaf inventory, command matrix, README, and execution-boundary notes for Ctrl-C, reboot, and Windows.
