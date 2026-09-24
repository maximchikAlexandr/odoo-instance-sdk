## 1. Owner-Neutral Runtime Identity

- [ ] 1.1 Extend the persisted runtime identity projection and catalogue protocol to address the exact `environment | project` owner while retaining all current PID/create-time and live-process evidence.
- [ ] 1.2 Add an atomic conditional catalogue cleanup keyed by `owner_kind`, `owner_id`, `root_pid`, and `create_time`, preserve the environment compatibility helper, and verify project registration is not removed.
- [ ] 1.3 Derive environment expectations from recorded environment artifacts and project expectations from the initialized project's resolved command prefix, cwd, and effective config used by detached launch.

## 2. Shared Safe Stop Path

- [ ] 2.1 Generalize the existing stop command plan and execution revalidation to both owner kinds while preserving fail-closed mismatch handling, POSIX/Windows process-group behavior, bounded termination, exit verification, vanished-process idempotency, and conditional cleanup.
- [ ] 2.2 Keep the existing environment-oriented SDK entry point compatible while exposing the owner-neutral result fields required by the CLI without adding a second termination algorithm.

## 3. CLI Contract

- [ ] 3.1 Remove the environment-only narrowing from the top-level `stop` callback, use the resolved runtime owner, and update help plus Rich/JSON/TOON context/result fields for project and environment identities.
- [ ] 3.2 Preserve the single `stop` public leaf and its existing dry-run, output-mode, provenance, exit-code, and error-redaction behavior.

## 4. Regression Verification

- [ ] 4.1 Parameterize the safe-stop identity suite across project and environment owners, covering match, mismatch/PID reuse, inaccessible evidence, plan-versus-execution changes, vanished process, idempotent no-row behavior, and conditional-cleanup races.
- [ ] 4.2 Extend CLI help and output-parity tests to assert both owner kinds, nullable environment fields for projects, and unchanged environment selection behavior.
- [ ] 4.3 Run the focused runtime-stop, catalogue-runtime, CLI output/help, type, lint, and repository OpenSpec validation checks; record any unrelated baseline failures without weakening the acceptance contract.
