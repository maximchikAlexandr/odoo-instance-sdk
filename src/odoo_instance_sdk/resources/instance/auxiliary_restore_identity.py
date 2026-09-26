from __future__ import annotations

import ipaddress
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

import psutil

from odoo_instance_sdk.internal.address import normalize_bind_host
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.instance.runtime import (
    _build_cli_args,
    _canonical_runtime_argv,
    _canonical_runtime_path,
    _runtime_config_arg,
    _runtime_expectations,
    _RuntimeBinding,
    _RuntimeCatalog,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.resources.instance import OdooInstance


def _listener_owner_pids(config: StartConfig) -> set[int] | None:  # noqa: C901
    """Return exact local listener owners, or ``None`` when inspection failed."""
    target = normalize_bind_host(config.http_interface)
    try:
        target_ip = ipaddress.ip_address(target)
    except ValueError:
        target_ip = None
    owners: set[int] = set()
    try:
        connections = psutil.net_connections(kind="tcp")
    except (OSError, psutil.Error):
        return None
    for connection in connections:
        if connection.status != psutil.CONN_LISTEN or connection.pid is None:
            continue
        address = connection.laddr
        address_host = getattr(address, "ip", address[0] if address else "")
        address_port = getattr(address, "port", address[1] if len(address) > 1 else None)
        if address_port != config.http_port:
            continue
        try:
            address_ip = ipaddress.ip_address(str(address_host))
        except ValueError:
            if str(address_host).lower() != target.lower():
                continue
        else:
            if not address_ip.is_unspecified and address_ip != target_ip:
                continue
            if (
                address_ip.is_unspecified
                and target_ip is not None
                and address_ip.version != target_ip.version
            ):
                continue
        owners.add(int(connection.pid))
    return owners


def _socket_owned_by(config: StartConfig, pid: int) -> bool:
    return _listener_owner_pids(config) == {pid}


def _expected_runtime_identity(
    instance: OdooInstance,
    config: StartConfig,
    environment: Mapping[str, JsonValue] | None,
) -> tuple[str, tuple[str, ...], str | None, str | None]:
    if environment is not None:
        return _runtime_expectations(environment)
    expected_argv = _canonical_runtime_argv(
        (*instance._executable_prefix(), *_build_cli_args(config))
    )
    return (
        _canonical_runtime_path(str(instance._executable_prefix()[0])),
        expected_argv,
        (
            _canonical_runtime_path(str(instance.config.default_cwd))
            if instance.config.default_cwd is not None
            else None
        ),
        _canonical_runtime_path(config.config_path) if config.config_path is not None else None,
    )


def _runtime_process_matches(
    instance: OdooInstance,
    config: StartConfig,
    process: psutil.Process,
    environment: Mapping[str, JsonValue] | None,
    *,
    expected_argv: Sequence[str] | None = None,
    expected_cwd: str | Path | None = None,
) -> bool:
    expected_executable, identity_argv, identity_cwd, expected_config_path = (
        _expected_runtime_identity(instance, config, environment)
    )
    expected_argv = _canonical_runtime_argv(expected_argv or identity_argv)
    expected_cwd = (
        _canonical_runtime_path(str(expected_cwd)) if expected_cwd is not None else identity_cwd
    )
    live_argv = _canonical_runtime_argv(tuple(process.cmdline()))
    live_config_path = _runtime_config_arg(live_argv)
    live_cwd = _canonical_runtime_path(str(process.cwd()))
    return (
        _canonical_runtime_path(str(process.exe())) == expected_executable
        and live_argv == expected_argv
        and (expected_cwd is None or live_cwd == expected_cwd)
        and (_canonical_runtime_path(live_config_path) if live_config_path is not None else None)
        == expected_config_path
    )


def _runtime_row_matches(
    instance: OdooInstance,
    config: StartConfig,
    binding: _RuntimeBinding,
    runtime: Mapping[str, JsonValue],
    environment: Mapping[str, JsonValue] | None,
    *,
    require_socket_owner: bool = True,
    expected_argv: Sequence[str] | None = None,
    expected_cwd: str | Path | None = None,
) -> int | None:
    owner_kind = str(runtime["owner_kind"])
    if owner_kind == "project" and str(runtime["owner_id"]) != binding.owner_id:
        return None
    if owner_kind == "environment":
        if environment is None:
            return None
        if _canonical_runtime_path(str(environment["repository_root"])) != _canonical_runtime_path(
            str(binding.repository_root)
        ) or _canonical_runtime_path(str(environment["git_common_dir"])) != _canonical_runtime_path(
            str(binding.git_common_dir)
        ):
            return None
    if int(str(runtime["http_port"])) != config.http_port or str(runtime["http_url"]).rstrip(
        "/"
    ) != instance.config.base_url.rstrip("/"):
        return None
    root_pid = int(str(runtime["root_pid"]))
    process = psutil.Process(root_pid)
    if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
        return None
    if float(process.create_time()) != float(str(runtime["create_time"])):
        return None
    if not _runtime_process_matches(
        instance,
        config,
        process,
        environment,
        expected_argv=expected_argv,
        expected_cwd=expected_cwd,
    ):
        return None
    if require_socket_owner and not _socket_owned_by(config, root_pid):
        return None
    return root_pid


def _recorded_runtime_pid(
    instance: OdooInstance,
    config: StartConfig,
    *,
    require_socket_owner: bool = True,
    expected_argv: Sequence[str] | None = None,
    expected_cwd: str | Path | None = None,
) -> int | None:
    """Return a matching persisted runtime PID, failing closed on drift."""
    binding = instance._runtime_binding
    if binding is None:
        return None
    catalog = cast("_RuntimeCatalog", instance._client.get_catalog())
    snapshot_reader = getattr(catalog, "_monitor_snapshot_rows", None)
    if not callable(snapshot_reader):
        return None
    try:
        snapshot = snapshot_reader(project_id=binding.project_id)
        runtimes = getattr(snapshot, "project_runtimes", ())
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None
    environment_runtimes = tuple(
        (runtime, environment)
        for environment, runtime in getattr(snapshot, "environments", ())
        if runtime is not None
    )
    candidates = tuple((runtime, None) for runtime in runtimes) + environment_runtimes
    for runtime, environment in candidates:
        try:
            matched_pid = _runtime_row_matches(
                instance,
                config,
                binding,
                cast("Mapping[str, JsonValue]", runtime),
                cast("Mapping[str, JsonValue] | None", environment),
                require_socket_owner=require_socket_owner,
                expected_argv=expected_argv,
                expected_cwd=expected_cwd,
            )
        except (KeyError, OSError, RuntimeError, TypeError, ValueError, psutil.Error):
            continue
        if matched_pid is not None:
            return matched_pid
    return None


def _project_runtime_owns_port(instance: OdooInstance, config: StartConfig) -> bool:
    """Compatibility predicate for callers that only need process identity."""
    return _recorded_runtime_pid(instance, config, require_socket_owner=False) is not None
