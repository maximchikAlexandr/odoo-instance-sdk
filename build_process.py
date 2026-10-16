"""Build-backend compatibility imports for the shared process boundary."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_BUILD_MODULE = (
    Path(__file__).resolve().parent / "src" / "odoo_instance_sdk" / "internal" / "proc" / "build.py"
)
_BUILD_SPEC = importlib.util.spec_from_file_location(
    "_odoo_instance_sdk_build_process", _BUILD_MODULE
)
if _BUILD_SPEC is None or _BUILD_SPEC.loader is None:
    raise RuntimeError("shared build process boundary is unavailable")
_BUILD_LOADED = importlib.util.module_from_spec(_BUILD_SPEC)
sys.modules[_BUILD_SPEC.name] = _BUILD_LOADED
_BUILD_SPEC.loader.exec_module(_BUILD_LOADED)

BuildProcessPolicy = _BUILD_LOADED.BuildProcessPolicy
run_build_process = _BUILD_LOADED.run_build_process

__all__ = ["BuildProcessPolicy", "run_build_process"]
