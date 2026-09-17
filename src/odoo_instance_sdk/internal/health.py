from __future__ import annotations

import json
import time
from collections.abc import Callable
from http import HTTPStatus
from typing import cast

import httpx

from odoo_instance_sdk.exceptions import (
    ProcessExitedBeforeReady,
    ReadinessTimeoutError,
)
from odoo_instance_sdk.internal.urls import assert_local
from odoo_instance_sdk.models import ModuleJsonValue, ReadinessResult


def _readiness_status(
    data: dict[str, ModuleJsonValue], *, version_info: bool, database_manager: bool
) -> str | None:
    if database_manager:
        return "pass" if isinstance(data.get("result"), list) else None
    if version_info:
        result = data.get("result")
        return "pass" if isinstance(result, dict) and result.get("server_version") else None
    status = data.get("status")
    return status if isinstance(status, str) else None


def _request_readiness(
    http: httpx.Client, url: str, *, version_info: bool, database_manager: bool
) -> httpx.Response:
    if database_manager:
        return http.post(
            url,
            json={"jsonrpc": "2.0", "method": "call", "params": {}},
        )
    if version_info:
        return http.post(url, json={})
    return http.get(url)


def poll_health(
    base_url: str,
    *,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
    alive_check: Callable[[], bool] | None = None,
    version_info: bool = False,
    database_manager: bool = False,
) -> ReadinessResult:
    assert_local(base_url)
    start = time.perf_counter()
    attempts = 0
    last_status: str | None = None
    if database_manager:
        health_url = f"{base_url.rstrip('/')}/web/database/list"
    elif version_info:
        health_url = f"{base_url.rstrip('/')}/web/webclient/version_info"
    else:
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
                response = _request_readiness(
                    http,
                    health_url,
                    version_info=version_info,
                    database_manager=database_manager,
                )
                if response.status_code == HTTPStatus.OK:
                    data = cast("dict[str, ModuleJsonValue]", response.json())
                    status = _readiness_status(
                        data,
                        version_info=version_info,
                        database_manager=database_manager,
                    )
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
