"""Focused canonical public-leaf scenarios for the full E2E tier."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.real_odoo.archive import ArchiveIdentity
from tests.integration.real_odoo.cleanup import FailureEvidence
from tests.integration.real_odoo.conftest import E2ERuntime
from tests.integration.real_odoo.focused_handlers import invoke_case as _invoke_case
from tests.integration.real_odoo.focused_support import (
    _approve_project_postgres,
    _bind_catalog_path,
    _ensure_isolated_environment,
    _isolated_catalog,
)
from tests.integration.real_odoo.focused_support import (
    focused_catalog as _focused_catalog_fixture,
)
from tests.integration.real_odoo.focused_support import (
    focused_project as _focused_project_fixture,
)
from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES, PublicLeafCase

pytestmark = [
    pytest.mark.real_odoo,
    pytest.mark.e2e_full,
    pytest.mark.serial,
    pytest.mark.timeout(300),
]
focused_catalog = _focused_catalog_fixture
focused_project = _focused_project_fixture


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in PUBLIC_LEAF_CASES
        if case.e2e_disposition == "focused" and case.e2e_evidence != ("E2E-FC-05",)
    ],
    ids=lambda case: ".".join(case.path),
)
def test_remaining_focused_public_leaves_use_canonical_inventory(
    case: PublicLeafCase,
    target_runtime: E2ERuntime,
    source_backup: ArchiveIdentity,
    failure_evidence: FailureEvidence,
    record_property: object,
    focused_project: Path,
    focused_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = _isolated_catalog(focused_catalog, tmp_path)
    _ensure_isolated_environment(catalog_path, focused_project)
    environment = {
        **target_runtime.environment,
        "HOME": str(catalog_path.parent.parent),
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_E2E_KEEP_FAILED": "1",
        "ODCLI_TEST_MASTER_PASSWORD": failure_evidence.secret_canary,
    }
    _approve_project_postgres(focused_project, environment)
    _bind_catalog_path(monkeypatch, catalog_path)
    _invoke_case(
        case,
        project=focused_project,
        runtime=target_runtime,
        evidence=failure_evidence,
        record_property=record_property,
        source_backup=source_backup,
        catalog_path=catalog_path,
    )
