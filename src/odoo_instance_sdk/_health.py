from __future__ import annotations

import json
import time
from collections.abc import Callable

import httpx

from odoo_instance_sdk._local_guard import warn_if_cleartext_auth
from odoo_instance_sdk.exceptions import (
    ProcessExitedBeforeReady,
    ReadinessTimeoutError,
)
from odoo_instance_sdk.models import OdooClientConfig, ReadinessResult


def poll_health(
    config: OdooClientConfig,
    *,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
    alive_check: Callable[[], bool] | None = None,
) -> ReadinessResult:
    start = time.perf_counter()
    attempts = 0
    last_status: str | None = None
    health_url = f"{config.base_url.rstrip('/')}/web/health?db_server_status=true"

    warn_if_cleartext_auth(config.base_url, stacklevel=2)

    with httpx.Client(
        auth=("admin", config.master_pwd),
        timeout=httpx.Timeout(config.http_timeout),
    ) as http:
        while True:
            elapsed = time.perf_counter() - start

            if elapsed >= timeout:
                raise ReadinessTimeoutError(timeout=timeout, last_status=last_status)

            if alive_check is not None and not alive_check():
                raise ProcessExitedBeforeReady("Linked process exited before readiness was reached")

            attempts += 1

            try:
                response = http.get(health_url)
                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status")
                    if status == "pass":
                        return ReadinessResult(
                            ok=True,
                            elapsed=time.perf_counter() - start,
                            attempts=attempts,
                            final_status=status,
                        )
                    last_status = status
            except (httpx.HTTPError, json.JSONDecodeError):
                pass

            time.sleep(poll_interval)
