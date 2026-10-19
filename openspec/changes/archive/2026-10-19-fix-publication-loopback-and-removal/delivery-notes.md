# Verification

## Scope and specification review

Reviewed the current `instance-publication` settings and lifecycle requirements and the `development-environment` removal guards. The delta preserves the existing contract and makes explicit IPv6 loopback acceptance, existing/missing worktree reconciliation, local-only removal and preflight refusal without route mutation. Canonical specs remain unchanged until the change is accepted and archived.

The initial local fix retained the publication action but ran it before removal preflight. The final implementation moves the existing action into the guarded removal path, after preflight and retained-evidence validation and before artifact deletion. No second preflight is introduced.

## Automated checks

- Python 3.13: publication and environment-removal group: **93 passed**.
- Python 3.12: all **16 new regression cases passed**.
- Python 3.13: separate monitor-publication, checkout and alternate-root acceptance group: **100 passed**.
- Full `make lint` passed, including Ruff lint/format, Alembic catalogue checks and the production file-size limit. Production mypy passed through the commit hook.
- Red evidence: the IPv6 setting, missing cleanup and cleanup-failure preservation tests failed on the original implementation. The dirty-worktree and occupied-port no-publication-mutation tests failed on the initial local fix and pass with the final ordering.
- `openspec validate fix-publication-loopback-and-removal --strict`: passed.
- `openspec validate instance-publication --type spec --strict`: passed (existing informational long-requirement notices only).

Reproduce the focused group from the repository root:

```sh
uv run --group test pytest tests/unit/test_publication.py tests/unit/resources/test_environment_remove.py -q
```

### Existing mixed-collection test failures

A combined 193-test invocation returned **189 passed, 4 failed**. All four failures assert that the temporary catalogue does not exist:

- `TestCheckoutPreflight.test_public_checkout_plan_is_typed_and_secret_free[shared-None]`
- `TestCheckoutPreflight.test_public_checkout_plan_is_typed_and_secret_free[copy-copy-target]`
- `TestCheckoutCopy.test_copy_existing_target_rolls_back_artifacts`
- `TestCheckoutCopy.test_copy_unavailable_source_rolls_back_artifacts`

They reproduce with the two production files restored from unmodified base `d6a4e093` when the same file list is collected, and all four pass when the checkout file is selected alone. The root `project_manifest` fixture pre-registers a catalogue, unlike the nested resource fixture; this change does not modify either fixture. No mixed-run success is claimed.

The mixed collection was:

```sh
uv run --group test pytest tests/unit/test_publication.py tests/unit/resources/test_environment_remove.py tests/unit/test_monitor_publication.py tests/unit/resources/test_environment_checkout.py tests/integration/test_alternate_tool_acceptance.py -q
```

Selecting that same file list with `-k 'public_checkout_plan_is_typed or copy_existing_target_rolls_back or copy_unavailable_source_rolls_back'` reproduces the four failures on the unmodified production baseline. Selecting only `tests/unit/resources/test_environment_checkout.py` with that filter passes all four.

## Local CLI and Caddy acceptance

A dedicated temporary state root, disposable Git fixture and separate live Caddy process were used with the locally installed CLI. Settings contained both `127.0.0.1` and `::1`. For both an existing SDK-owned worktree and an already absent worktree:

1. CLI checkout created a disposable shared-mode environment.
2. Its fixture route was loaded into the dedicated Caddy process.
3. `env rm <uuid> --yes --format json` completed.
4. The owner route disappeared from both the persisted file and live Caddy configuration; owned worktree/configuration cleanup completed.

Canonical package files and publication settings remained unchanged. Odoo and dependency tooling were test substitutes; this verifies local Caddy validation/reload and CLI removal, not a real Odoo/database session, production HTTPS, DNS or browser authentication.

## Deployment limits

No new dependencies, public flags, schema or migration. This fix does not address alternate installer provenance, local unpublished installation support or cross-root locks for shared Odoo/PostgreSQL resources. It does not authorize concurrent shared-resource mutation by independently rooted tools.
