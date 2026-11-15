## 1. Persisted port intent

- [x] 1.1 Add validated optional `ProjectConfig.assigned_http_port` load/render/round-trip support while preserving legacy manifests and the distinct `preferred_http_port` contract.
- [x] 1.2 Extend the shared project HTTP resolver and owned generated-config writers to apply preferred, assigned, configured, and default precedence without editing source Odoo configuration.

## 2. Main-project run coordination

- [x] 2.1 Implement locked main-project port selection that exact-validates explicit preferences, reuses or replaces automatic assignments through `find_free_port()`, atomically persists changes, and reconciles owned generated configuration before command capture.
- [x] 2.2 Wire the coordinator into real foreground and detached main-project run preparation while keeping dry-run, status, publication, task-environment, and auxiliary-restore construction free of hidden writes or duplicate allocation.
- [x] 2.3 Extend the detached typed result and Rich/machine rendering, plus the foreground fallback notice, with bounded selected-port provenance and stable fallback reasons.

## 3. Deterministic verification

- [x] 3.1 Add parametrized model, resolver, allocator, and generated-config tests for legacy/round-trip precedence, default fallback, assignment reuse/replacement, explicit conflicts, external source immutability, and concurrent/failed updates.
- [x] 3.2 Add focused foreground/detached CLI and runtime tests proving dry-run inertness, exact command/readiness/runtime endpoints, final spawn-race rejection, typed output in every supported mode, restart reuse, and unchanged environment/auxiliary behavior.
- [x] 3.3 Add status/publication regression coverage proving both consumers use the captured selected endpoint rather than stale manifest/source-config values.
- [x] 3.4 Update public configuration/output documentation and compatibility notes for automatic assignment, explicit-pin recovery, and rollback of manifests containing `assigned_http_port`.

## 4. Delivery gates

- [x] 4.1 Run focused unit/integration, formatting, lint, type, CLI characterization, and strict OpenSpec checks; publish the exact candidate revision for CI without splitting any automatic suite to bypass the ten-minute local limit.
- [x] 4.2 Provide the Implementer-owned GitHub tests/fixtures required by the verification matrix; the independent Tester SHALL only inspect their coverage and execute the delivery-plan walkthrough on the consolidated candidate.
