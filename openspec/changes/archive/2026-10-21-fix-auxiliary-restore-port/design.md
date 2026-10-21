## Context
The source instance is reconstructed for restore; a running primary may legitimately fail its exact argv/cwd/config identity test. Reusing an unproven listener would expose master credentials.

## Goals / Non-Goals
Keep the primary untouched, preserve immutable launch snapshots and secret-request identity checks. No supervisor rewrite, public port flag, catalogue schema change or live-instance test.

## Decisions
Capture a free loopback HTTP port through `find_free_port`, excluding the source port. Keep source and helper instances separate: source identity/reuse stays unchanged; helper readiness and owned listener proof use the captured helper config. Only HTTP resources owned by the active session resolve its effective endpoint. The original config and catalogue bindings remain unchanged. Recheck the captured helper port before spawn; a race fails closed instead of rebuilding the command.

## Risks / Trade-offs
Port allocation is not a cross-process reservation. Execution revalidation rejects a port acquired after planning. A proven existing runtime can still be reused, without stopping it.

## Validation
Regression tests hold a real primary loopback listener open while recording the helper process boundary; verify distinct helper HTTP/transport, identity rejection, original config preservation and selective cleanup. Run existing restore/COPY/adoption tests, lint, typing and OpenSpec validation. No permanent instance is touched.
