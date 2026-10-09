"""Typed, metadata-only machine operation contracts."""

from odoo_instance_sdk.operations.contracts import *  # noqa: F403
from odoo_instance_sdk.operations.invoke import (  # noqa: F401
    LocalInvocation,
    OperationInvokeError,
    build_operation_command,
    decode_request,
    failure_invocation,
    invoke_local,
    invoke_with_context,
)
from odoo_instance_sdk.operations.session import (  # noqa: F401
    SessionDecision,
    SessionEvent,
    SessionLimits,
    SessionOutcome,
    SessionRequest,
    run_approval_session,
)
