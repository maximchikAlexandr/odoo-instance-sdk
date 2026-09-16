"""Compatibility re-export shim."""

from __future__ import annotations

import importlib

from odoo_instance_sdk.internal.dbprep.materialize import (
    DatabasePreparationCoordinator as DatabasePreparationCoordinator,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    DatabasePreparationFailureContext as DatabasePreparationFailureContext,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _CatalogueRestoreSource as _CatalogueRestoreSource,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _open_verified_zip as _open_verified_zip,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _planned_project_identity as _planned_project_identity,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    _remote_password as _remote_password,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    canonical_project_identity as canonical_project_identity,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    classify_freshness as classify_freshness,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    compare_provenance as compare_provenance,
)
from odoo_instance_sdk.internal.dbprep.source_1 import (
    resolve_runtime_binding as resolve_runtime_binding,
)

_package = importlib.import_module("odoo_instance_sdk.internal.dbprep")
for _key, _value in _package.__dict__.items():
    if _key.startswith("__"):
        continue
    globals()[_key] = _value

__all__ = [
    "DatabasePreparationFailureContext",
    "_CatalogueRestoreSource",
    "_open_verified_zip",
    "_planned_project_identity",
    "canonical_project_identity",
    "classify_freshness",
    "compare_provenance",
]
