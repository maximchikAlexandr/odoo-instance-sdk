from __future__ import annotations

import json
import time
from collections.abc import Callable
from http import HTTPStatus

import httpx

from odoo_instance_sdk.exceptions import (
    ProcessExitedBeforeReady,
    ReadinessTimeoutError,
)
from odoo_instance_sdk.internal.urls import assert_local
from odoo_instance_sdk.models import ReadinessResult


def poll_health(
    base_url: str,
    *,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
    alive_check: Callable[[], bool] | None = None,
) -> ReadinessResult:
    assert_local(base_url)
    start = time.perf_counter()
    attempts = 0
    last_status: str | None = None
    health_url = f"{base_url.rstrip('/')}/web/health?db_server_status=true"

    with httpx.Client(timeout=httpx.Timeout(timeout)) as http:
        while True:
            elapsed = time.perf_counter() - start

            if elapsed >= timeout:
                raise ReadinessTimeoutError(timeout=timeout, last_status=last_status)

            if alive_check is not None and not alive_check():
                raise ProcessExitedBeforeReady("Linked process exited before readiness was reached")

            attempts += 1

            try:
                response = http.get(health_url)
                if response.status_code == HTTPStatus.OK:
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
