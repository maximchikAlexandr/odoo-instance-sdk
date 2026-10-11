## Context

PR #140 is currently at `92406fa537eacf048479162197fe30faab718e6c` on base `4a9369c3cab0f9a34bf3d3b2411f3ec0ac5bf5e6`. It already contains the useful core: `internal/alternate_tool.py` owns exact-SHA uv installation, provenance, selector probing, manifest/shim rendering, lifecycle locks, inspection and cleanup for both numbered and hot-fix identities; `internal/paths.py`, storage migration and self-update enforce root confinement. The overreach is `commands/slot.py` plus public CLI registration/output/docs/tests.

The inspected GitLab `binta-test-repo` snapshot `d21521c985263a097e6bcf042d132db4d80e137c` has a 209-line `scripts/agent_checkout.py`. Only local independent-clone creation, reference-SHA/origin checks, marker/hash verification, dirty/unpushed guards, and contained external-checkout deletion remain candidates for extraction. Current SDK `resources/environment/checkout_api.py` already exposes `adopt_command()`/`adopt()` for a caller-owned clone, while `resources/environment/cleanup.py::_do_remove()` already removes the COPY database and owned backup, generated config/lock, and owned venv without deleting that caller-owned checkout. Binta-owned policy remains in GitLab. `seed_odoo.py`, diagnostics, and administrator-password setup are separate and must remain there.

## Goals / Non-Goals

**Goals:**

- no public `odcli slot` command;
- one lifecycle engine for `odcli-N` and `odcli-fix-ISSUE`;
- atomic smallest-free-number selection across launcher, state, manifest/manager root, and linked checkout;
- one private SDK coordinator limited to numbered allocation/reservation, independent clone/origin/marker, fixed linkage, external-checkout inspection and post-environment-cleanup deletion;
- one verified SDK-owned Python environment per numbered checkout, using the existing owned-environment path rather than a second environment manager;
- a thin GitLab wrapper that supplies Binta configuration without copying reusable mechanics;
- explicit keep/remove mapping for PR #140 and repository ownership for every cross-repo concern;
- fail-closed rollback and cleanup that never overwrites or deletes orphaned/foreign data.

**Non-Goals:**

- a public SDK/Click API, generic plugin/callback framework, daemon, registry database, arbitrary repository manager, or Windows lifecycle port;
- a second uv/provenance/manifest/shim/lock/remove implementation;
- automatic adoption of old hot-fix state or orphaned numbered/checkouts;
- moving `seed_odoo.py`, DB diagnostics, admin-password setup, secrets, persistent service policy, or Binta fixtures into the SDK;
- making `~/.odcli-N` substitute for separate database, filestore, port, venv, or checkout isolation.

## Decisions

### D1. Preserve the proven shared lifecycle; delete only the public adapter

`src/odoo_instance_sdk/internal/alternate_tool.py` remains the only owner of alternate identity, exact-SHA uv install, installed provenance, selector capability, manifest/shim, full-lifetime lock, inspection, and tool/state cleanup. `fix_tool.py` remains a thin canonical-interpreter plus GitHub PR/reviewer/retirement policy adapter. The new numbered service calls the same private module; it does not reimplement lifecycle mechanics.

The alternative—reverting the whole PR—would discard working root isolation and hot-fix convergence. Keeping `commands/slot.py` is rejected because the user explicitly does not want a public product surface.

### D2. One private package coordinator owns numbered allocation and checkout linkage

Add a narrow private module `src/odoo_instance_sdk/internal/agent_checkout.py`, invoked only through a thin service/skill script using the trusted canonical interpreter. It is not registered in Click or package console scripts.

For create, the coordinator takes the requested full SDK SHA plus an explicit environment configuration supplied by the calling repository. Under `~/.local/share/odcli-slots/allocation.lock`, it scans positive integers from 1 and selects the first identity for which all four dimensions are absent:

1. `~/.local/bin/odcli-N`;
2. `~/.odcli-N`;
3. `~/.local/share/odcli-slots/N` including manifest/reservation;
4. configured checkout `<checkout-parent>/odcli-N`.

Any symlink, partial directory, invalid marker, legacy manifest, or lone artifact marks that number occupied and is reported; it is never repaired, adopted, or overwritten. The coordinator writes an exclusive reservation before releasing selection, retains the allocation lock through publication, and records the verified checkout path/source SHA in the fixed numbered manifest. Hot-fix manifests never gain checkout fields.

The alternative of letting GitLab choose a number races with launcher/state creation. A generic registry or extensible metadata map is unnecessary; deterministic paths and fixed fields are sufficient.

### D3. Coordinator wraps the existing caller-owned environment API

The coordinator owns only clean reference/origin verification, `git clone --local --no-hardlinks`, origin rewrite, a fixed ownership marker/hash, read-only dirty/unpushed/containment inspection, and deletion of that external checkout. The GitLab wrapper supplies the existing `ProjectConfig` and `EnvironmentCheckoutOptions` inputs, including explicit base/source, name, COPY target, base Python, audited lock/digest, Odoo bin, private config source and optional requested HTTP port. The coordinator passes the caller-owned clone directly to `adopt_command()`/`adopt()`.

The existing environment lifecycle remains the sole owner of generated config and bounded rewrites, port allocation/revalidation, database/backup restore and catalogue journal, owned venv synchronization/verification, runtime linkage, retry/rollback and removal of DB/backup/config/lock/venv. No current API gap was found in the inspected snapshot. If implementation proves a required option cannot traverse `EnvironmentCheckoutOptions`, the only permitted response is the smallest typed extension to that existing API plus its focused test; the coordinator SHALL NOT implement the missing lifecycle step.

Inputs are explicit and validated by their existing owners; secrets are never printed, returned, committed or embedded in the numbered manifest. GitLab keeps a short wrapper that supplies concrete values. There is no arbitrary hook, schema, provider interface, repository plug-in point, second config rewriter, port allocator, restore/drop sequence, catalogue writer, or environment cleanup.

### D4. Creation and removal are one fail-closed service workflow

Creation reserves `N`, stages the external checkout and tool, then calls the existing environment adoption command. Only after adoption returns a ready caller-owned environment does the coordinator verify marker, environment ID/path/lock digest, resolved interpreter and alternate-tool provenance and atomically publish linkage. A numbered manifest records that environment identity; a hot-fix manifest does not. Adoption failure uses its existing recovery. A later pre-publication failure invokes existing environment `remove()` once before the coordinator deletes only marked attempt-owned checkout/tool/reservation artifacts; if removal fails, linkage is retained for retry.

Removal acquires allocation and identity locks, revalidates manifest/marker/linkage, and performs only external-checkout guards that the caller-owned environment lifecycle deliberately does not own: symlink/path escape, dirty or unpushed Git state, and marker/origin mismatch. It then calls existing environment `remove()` exactly once. That path owns running-runtime, config/lock/interpreter/environment/database evidence and removes COPY DB/owned backup/config/lock/venv while preserving the caller-owned checkout. Only after `remove()` succeeds does the coordinator delete the marked external checkout and delegate tool/state removal to the shared alternate lifecycle. If checkout deletion fails, linkage and evidence remain retryable; the coordinator never repeats DB/config/venv cleanup itself.

A two-phase public API and arbitrary rollback callbacks are rejected. The only supported entry is the private coordinator plus its fixed GitLab configuration wrapper.

### D5. Runtime root and update contracts stay narrow

The current central `ODCLI_USER_ROOT` provider, canonical-only legacy migration, hot-fix/numbered launcher context, and self-update guards are retained. The numbered rejection message points to the service/skill workflow, not `odcli slot`. Canonical `odcli update` still neither discovers nor changes alternates. `HOME` stays unchanged.

### D6. PR #140 keep/remove map

| PR #140 surface | Decision | Final owner / evidence |
| --- | --- | --- |
| `internal/alternate_tool.py`, packaging assertion, shared-engine tests | **Keep and narrow** | SDK; one lifecycle for both identities, fixed numbered checkout/environment linkage only |
| `resources/environment/checkout_api.py` adoption and `cleanup.py` removal | **Reuse as the only environment lifecycle** | SDK; owns config/port/COPY DB/catalogue/venv create and cleanup for caller-owned checkout |
| `internal/paths.py`, `storage_migration.py`, `self_update.py`, `commands/update.py` and focused tests | **Keep and adapt** | SDK; root confinement and update guidance without public slot references |
| `.agents/skills/odcli-autonomous-work/**` | **Keep and adapt** | SDK skill; hot-fix policy stays thin, numbered/checkout wrapper invokes private coordinator |
| `tests/integration/test_alternate_tool_acceptance.py`, `test_alternate_tool.py`, migration/update tests | **Keep and refocus** | SDK; reuse for private service and cross-root evidence |
| `src/odoo_instance_sdk/commands/slot.py`, `tests/unit/test_slot_commands.py` | **Delete** | No replacement public command; private coordinator tests cover management |
| slot import/registration in `commands/cli_parts/registration.py` | **Delete** | Root help proves `slot` absent |
| slot-only `commands/output.py` details changes | **Delete unless another existing caller proves need** | SDK; no speculative retention |
| slot additions in CLI characterization/surface/output inventories and real-Odoo command matrix/contracts | **Delete** | Public leaf count and matrix return to the non-slot surface |
| README and `docs/execution-boundary.md` public slot sections | **Replace** | SDK docs describe service/skill workflow and explicit isolation boundary |
| architecture inventory/guard | **Keep and update** | SDK; allows allocation/external-checkout coordinator only; forbids duplicate config rewrite, port allocation, restore, DB drop, catalogue and environment cleanup mechanics |
| OpenSpec artifacts | **Replace with this revision** | Planning-only commit |
| GitLab `scripts/agent_checkout.py` | **Reduce to thin wrapper/config** | Separate GitLab MR |
| GitLab `seed_odoo.py`, diagnostics, `set_admin_password.py`, secrets/stand policy | **Keep unchanged** | GitLab only |

### D7. Repository ownership and delivery split

The SDK PR owns allocation/external-checkout coordination, invocation of existing environment adoption/removal without duplicating it, shared alternate-tool linkage, trusted bootstrap, root/update behavior, tests, packaging, SDK skill/docs, and removal of public CLI changes. The GitLab MR owns concrete existing-API inputs and Binta scenarios/docs. SDK acceptance uses fakes/temp directories for external commands; GitLab acceptance proves the real wrapper contract without committing secrets.

Neither repository change may merge as a claim of complete delivery until exact SDK and GitLab revisions are cross-referenced in evidence. A missing GitLab write path is an explicit external blocker, not permission to fold Binta policy into the SDK.

### D8. Acceptance targets mechanism and negative boundaries

Tests cover concurrent allocation, orphan dimensions, exact-SHA provenance, adoption failure recovery, manifest/checkout/environment linkage, external Git guards before removal, successful environment-remove-before-checkout-delete ordering, and neighbor preservation. Integrated acceptance runs canonical, two numbered and two hot-fix roots with distinct checkout/database/filestore/port/venv identities. Architecture checks prove there is no public `slot` leaf and no second installer/probe/renderer/lock, config rewriter, port allocator, restore/DB-drop/catalogue path, venv builder or environment cleanup.

## Ponytail Gate

| Candidate subsystem | Existing alternative | Decision |
| --- | --- | --- |
| Alternate lifecycle | Implemented `internal/alternate_tool.py` in PR #140 | Keep; do not create another engine |
| Public numbered CLI | Skill/service workflow is sufficient | Delete `commands/slot.py` and all public surface |
| Number allocator | No race-safe allocator exists | Keep one small allocator inside private coordinator |
| Checkout coordinator | Existing GitLab clone/marker guards plus SDK caller-owned adoption | Keep only allocation and external-checkout boundary; delete lifecycle duplication |
| Environment manager | Existing SDK `adopt_command()`/`adopt()` and `remove()` | Reuse for config/port/DB/catalogue/venv lifecycle; reject any parallel implementation |
| Generic provider/plugin/callback layer | No second consumer or requirement | Reject |
| Registry DB/daemon | Deterministic paths, marker and `flock` suffice | Reject |
| Automatic orphan repair/migration | Could overwrite foreign state | Reject; occupied and fail closed |
| Seed/diagnostics/admin-password helpers | Already GitLab-specific and separate | Leave in GitLab |
| New dependency | Stdlib and existing SDK process boundary suffice | Reject |

## Risks / Trade-offs

- **[Long create operation holds allocation lock]** → correctness wins over allocation throughput; bounded local clone/restore is a rare service operation and avoids reservation state machinery.
- **[GitLab snapshot or environment differs]** → wrapper validates every required value and keeps the MR blocked on mismatch; no SDK default guesses Binta paths.
- **[Adoption or removal fails mid-lifecycle]** → rely on the existing catalogue journal/retry contract; coordinator retains reservation, marker and linkage and does not perform compensating DB/config/venv mutations.
- **[Per-checkout venv creation is slow or its lock drifts]** → reuse the existing hash-locked SDK environment path, record environment/lock identity, hold the allocation reservation until verification, and fail closed without adopting a shared venv.
- **[PR #140 removal accidentally weakens hot-fix isolation]** → retain shared-engine/root/update tests and add a keep/delete architecture audit.
- **[Private module becomes de facto public]** → no Click/console registration, no compatibility promise beyond the checked-in skill/GitLab wrappers, and docs call it service tooling.
- **[Cross-repo delivery drifts]** → acceptance records exact SDK/GitLab SHAs and wrapper contract version; mismatch blocks completion.

## Migration Plan

1. On top of current PR #140, land the allocation/external-checkout coordinator and fixed linkage while retaining the shared alternate engine and existing caller-owned environment adoption/removal.
2. Move numbered management to the checked-in service/skill wrapper and delete the public slot command, registration, output inventory and docs.
3. Adapt self-update guidance and refocus existing tests/architecture guards.
4. In a separate GitLab MR, reduce `scripts/agent_checkout.py` to environment/configuration plus invocation; keep Binta-only assets untouched.
5. Run SDK fake/external-boundary acceptance and GitLab real-wrapper acceptance, including distinct interpreter/site-package evidence for concurrent numbered checkouts; record both exact SHAs.
6. Rollback by using the verified private removal workflow for created numbered identities, reverting the GitLab wrapper MR, then reverting SDK changes. Never delete orphaned data as part of rollback.

## Open Questions

None. If GitLab write access or the inspected environment contract is unavailable during implementation, the GitLab MR is a declared blocker; the SDK design does not expand to compensate.
