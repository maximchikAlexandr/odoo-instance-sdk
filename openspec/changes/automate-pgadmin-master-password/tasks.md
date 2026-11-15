# Tasks

## 1. Private master password file

- [x] 1.1 Add `~/.odcli/pgadmin/master.env` generation, reuse, and fail-closed validation in pgAdmin file preparation, and verify unit tests cover first write, reuse, symlink, mode, extra keys, and absence from mounts
- [x] 1.2 Keep the file out of Docker argv, environment, and public plan projections, and verify the existing secret-free command snapshot tests still pass with the new file

## 2. Unlock and database proof

- [x] 2.1 Require `PGADMIN_CONFIG_USE_OS_SECRET_STORAGE=False` on create and on container match, and verify a container missing that variable is not reused
- [x] 2.2 Add the loopback `pgadmin.master_password.unlock` action and the captured `pgadmin.container.authenticate` `SELECT 1` step to both reconciliation branches, and verify unit tests reject a failed unlock, a rejected query, and an existing profile that cannot be unlocked without calling reset
- [x] 2.3 Extend the opt-in pinned-image smoke test for first open, reopen, and denied authentication, and verify the test module still skips unless `ODCLI_PGADMIN_DOCKER_SMOKE=1`

## 3. Dashboard

- [x] 3.1 Restore Open pgAdmin on environment cards from generated `pgadmin.state`, and verify frontend tests cover an eligible click and a disabled ineligible state
- [x] 3.2 Update the local-pgadmin purpose text so it no longer calls the dashboard flow deferred, and verify `openspec validate --change automate-pgadmin-master-password --strict` succeeds
