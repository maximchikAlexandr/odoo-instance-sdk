"""Bounded discovery of installed machine-operation providers.

Provider discovery is deliberately a small boundary around Python entry
points.  It does not inspect packages, install code, or keep a hot-reloadable
registry.  A provider is loaded once for the lifetime of the process and its
validated bindings are retained for later registry construction.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import TypeVar

from .contracts import CONTRACT_VERSION, ENTRY_POINT_GROUP, OperationBinding, provider_bindings

DEFAULT_STARTUP_DEADLINE_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    """Sanitized failure reported without exposing provider diagnostics."""

    provider: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class LoadedOperationProvider:
    """An entry point plus bindings validated before registry construction."""

    name: str
    contract_version: int
    _validated_bindings: tuple[OperationBinding, ...]

    def bindings(self) -> tuple[OperationBinding, ...]:
        return self._validated_bindings


@dataclass(frozen=True, slots=True)
class ProviderDiscovery:
    """One bounded discovery result for the selected Python interpreter."""

    providers: tuple[LoadedOperationProvider, ...] = ()
    failures: tuple[ProviderFailure, ...] = ()

    @property
    def bindings(self) -> tuple[OperationBinding, ...]:
        return tuple(binding for provider in self.providers for binding in provider.bindings())

    def raise_for_failures(self) -> None:
        if self.failures:
            first = self.failures[0]
            raise ProviderDiscoveryError(first)


class ProviderDiscoveryError(ValueError):
    """A bounded, sanitized provider discovery failure."""

    def __init__(self, failure: ProviderFailure) -> None:
        self.failure = failure
        super().__init__(f"{failure.code}: provider {failure.provider!r}: {failure.message}")


_cache_lock = threading.Lock()
_cache: dict[str, ProviderDiscovery | None] = {"discovery": None}
_T = TypeVar("_T")


def clear_provider_cache() -> None:
    """Clear the process-local discovery result for tests and fresh processes."""

    with _cache_lock:
        _cache["discovery"] = None


def _entry_point_name(entry_point: EntryPoint) -> str:
    return str(getattr(entry_point, "name", "<unnamed-provider>"))


def _entry_point_sort_key(entry_point: EntryPoint) -> tuple[str, str, str]:
    distribution = getattr(getattr(entry_point, "dist", None), "name", "") or ""
    return (_entry_point_name(entry_point), str(getattr(entry_point, "value", "")), distribution)


def _safe_exception_code(error: BaseException) -> str:
    if isinstance(error, (ValueError, TypeError)):
        return "provider_incompatible"
    return "provider_load_failed"


def _safe_message(code: str, error: BaseException | None = None) -> str:
    """Return bounded diagnostics without copying arbitrary plugin text."""

    if code == "provider_timeout":
        return "provider startup deadline exceeded"
    if code == "provider_incompatible":
        return "provider contract or binding is incompatible"
    if error is not None and isinstance(error, ImportError):
        return "provider could not be imported"
    return "provider failed during startup"


def _run_bounded(
    callback: Callable[[], _T], timeout: float
) -> tuple[_T | None, BaseException | None, bool]:
    """Run one provider callback with a daemon worker and a hard caller bound."""

    result: list[_T] = []
    error: list[BaseException] = []

    def worker() -> None:
        try:
            result.append(callback())
        except BaseException as exc:  # provider code is outside the core boundary
            error.append(exc)

    thread = threading.Thread(target=worker, name="odcli-operation-provider", daemon=True)
    thread.start()
    deadline = time.monotonic() + max(0.0, timeout)
    # A timed Thread.join can wait for an extension-owned thread on some
    # interpreters. Polling keeps the caller bounded; timed-out workers are
    # daemon threads and their result is intentionally discarded.
    while thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0)
    if thread.is_alive():
        return None, None, True
    if error:
        return None, error[0], False
    return result[0] if result else None, None, False


def _load_entry_point(
    entry_point: EntryPoint, timeout: float
) -> tuple[LoadedOperationProvider | None, ProviderFailure | None]:
    name = _entry_point_name(entry_point)
    started = time.monotonic()
    loaded, error, timed_out = _run_bounded(entry_point.load, timeout)
    if timed_out:
        return None, ProviderFailure(name, "provider_timeout", _safe_message("provider_timeout"))
    if error is not None:
        code = _safe_exception_code(error)
        return None, ProviderFailure(name, code, _safe_message(code, error))

    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        return None, ProviderFailure(name, "provider_timeout", _safe_message("provider_timeout"))
    rows, error, timed_out = _run_bounded(lambda: provider_bindings(loaded), remaining)
    if timed_out:
        return None, ProviderFailure(name, "provider_timeout", _safe_message("provider_timeout"))
    if error is not None:
        code = _safe_exception_code(error)
        return None, ProviderFailure(name, code, _safe_message(code, error))
    assert rows is not None
    version = getattr(loaded, "contract_version", CONTRACT_VERSION)
    return LoadedOperationProvider(name, version, rows), None


def _discover(discovered: Iterable[EntryPoint], deadline: float) -> ProviderDiscovery:
    started = time.monotonic()
    entries = tuple(sorted(discovered, key=_entry_point_sort_key))
    providers: list[LoadedOperationProvider] = []
    failures: list[ProviderFailure] = []
    for entry_point in entries:
        remaining = deadline - (time.monotonic() - started)
        if remaining <= 0:
            failures.append(
                ProviderFailure(
                    _entry_point_name(entry_point),
                    "provider_timeout",
                    _safe_message("provider_timeout"),
                )
            )
            continue
        provider, failure = _load_entry_point(entry_point, remaining)
        if provider is not None:
            providers.append(provider)
        if failure is not None:
            failures.append(failure)
    return ProviderDiscovery(tuple(providers), tuple(failures))


def discover_operation_providers(
    *,
    timeout_seconds: float = DEFAULT_STARTUP_DEADLINE_SECONDS,
    use_cache: bool = True,
    entry_point_factory: Callable[[], Iterable[EntryPoint]] | None = None,
) -> ProviderDiscovery:
    """Discover only the selected interpreter's installed operation providers.

    The default path is cached once per process.  Tests can inject an entry
    point source and disable the cache without changing production behavior.
    """

    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must not be negative")
    if use_cache and entry_point_factory is None:
        with _cache_lock:
            cached = _cache["discovery"]
            if cached is not None:
                return cached

    def get_entry_points() -> Iterable[EntryPoint]:
        if entry_point_factory is not None:
            return entry_point_factory()
        return entry_points(group=ENTRY_POINT_GROUP)

    started = time.monotonic()
    discovered, error, timed_out = _run_bounded(get_entry_points, timeout_seconds)
    if timed_out:
        result = ProviderDiscovery(
            failures=(
                ProviderFailure(
                    "<entry-point-group>",
                    "provider_timeout",
                    _safe_message("provider_timeout"),
                ),
            )
        )
    elif error is not None:
        code = _safe_exception_code(error)
        result = ProviderDiscovery(
            failures=(ProviderFailure("<entry-point-group>", code, _safe_message(code, error)),)
        )
    else:
        assert discovered is not None
        result = _discover(discovered, timeout_seconds - (time.monotonic() - started))
    if use_cache and entry_point_factory is None:
        with _cache_lock:
            if _cache["discovery"] is None:
                _cache["discovery"] = result
            cached = _cache["discovery"]
            assert cached is not None
            return cached
    return result


def discovered_bindings(
    *, timeout_seconds: float = DEFAULT_STARTUP_DEADLINE_SECONDS
) -> tuple[OperationBinding, ...]:
    """Return validated provider bindings or fail closed with a safe error."""

    discovery = discover_operation_providers(timeout_seconds=timeout_seconds)
    discovery.raise_for_failures()
    return discovery.bindings


def discover_providers(
    *,
    timeout_seconds: float = DEFAULT_STARTUP_DEADLINE_SECONDS,
    use_cache: bool = True,
    entry_point_factory: Callable[[], Iterable[EntryPoint]] | None = None,
) -> ProviderDiscovery:
    """Compatibility spelling for callers using the short provider API name."""

    return discover_operation_providers(
        timeout_seconds=timeout_seconds,
        use_cache=use_cache,
        entry_point_factory=entry_point_factory,
    )


__all__ = [
    "DEFAULT_STARTUP_DEADLINE_SECONDS",
    "LoadedOperationProvider",
    "ProviderDiscovery",
    "ProviderDiscoveryError",
    "ProviderFailure",
    "clear_provider_cache",
    "discover_operation_providers",
    "discover_providers",
    "discovered_bindings",
]
