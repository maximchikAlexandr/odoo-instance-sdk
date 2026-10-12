"""Select the runnable source Python before creating a COPY target venv."""

from pathlib import Path

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.resources.environment.checkout_artifacts import _resolve_python_bin
from odoo_instance_sdk.resources.environment.checkout_planning import _CheckoutPlan


def _source_auxiliary_python(plan: _CheckoutPlan) -> str:
    if not plan.python_owned:
        return plan.python_path
    source_python = plan.project.python
    if source_python is None:
        raise ConfigError("Source project Python is required for COPY checkout")
    python_bin = _resolve_python_bin(source_python, plan.repo_root)
    if not Path(python_bin).is_file():
        raise ConfigError("Source project Python is unavailable for COPY checkout")
    return python_bin
