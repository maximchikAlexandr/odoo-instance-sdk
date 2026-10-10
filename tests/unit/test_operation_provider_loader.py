from __future__ import annotations

from importlib.metadata import EntryPoint
from threading import Event
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    import msgspec

from odoo_instance_sdk.operations import (
    OperationBinding,
    OperationDescriptor,
    OperationRequest,
    OperationResult,
    ProviderDiscoveryError,
    build_registry,
    clear_provider_cache,
    contract_bundle,
    discover_operation_providers,
)


def test_discovery_is_deterministic_cached_and_exportable() -> None:
    calls = 0

    def provider_for(name: str) -> object:
        class Provider:
            contract_version = 1

            def provide(self) -> tuple[OperationBinding, ...]:
                return (
                    OperationBinding(
                        OperationDescriptor(
                            operation_id=f"fixture.{name}",
                            canonical_path=("fixture", name),
                            provider="fixture",
                            request_type=OperationRequest,
                            result_type=OperationResult,
                        ),
                        factory=lambda request, _context: request,
                    ),
                )

        return Provider()

    class LoadedEntryPoint(EntryPoint):
        def load(self) -> object:
            return provider_for(self.name)

    entries = (
        LoadedEntryPoint("z-provider", "unused", "odoo_instance_sdk.operations"),
        LoadedEntryPoint("a-provider", "unused", "odoo_instance_sdk.operations"),
    )

    def source() -> tuple[EntryPoint, ...]:
        nonlocal calls
        calls += 1
        return entries

    # Ordering is tested separately from registry conflict handling.
    result = discover_operation_providers(
        entry_point_factory=source,
        use_cache=False,
        timeout_seconds=0.2,
    )
    assert calls == 1
    assert [item.name for item in result.providers] == ["a-provider", "z-provider"]
    assert not result.failures
    bundle = cast("Any", contract_bundle(result.bindings))
    assert [item["id"] for item in bundle["operations"] if item["provider"] == "fixture"] == [
        "fixture.a-provider",
        "fixture.z-provider",
    ]


def test_incompatible_provider_is_reported_without_calling_operation_factory() -> None:
    called = False

    def factory(_: msgspec.Struct | None, _context: object) -> None:
        nonlocal called
        called = True
        return

    class Provider:
        contract_version = 99

        def provide(self) -> tuple[OperationBinding, ...]:
            return (
                OperationBinding(
                    OperationDescriptor(
                        operation_id="fixture.invalid",
                        canonical_path=("fixture", "invalid"),
                        provider="fixture",
                    ),
                    factory=factory,
                ),
            )

    class TestEntryPoint(EntryPoint):
        def load(self) -> object:
            return Provider()

    result = discover_operation_providers(
        entry_point_factory=lambda: (TestEntryPoint("invalid", "unused", "group"),),
        use_cache=False,
        timeout_seconds=0.2,
    )
    assert not result.providers
    assert result.failures[0].code == "provider_incompatible"
    assert "diagnostic" not in result.failures[0].message
    assert called is False


def test_slow_provider_is_bounded_and_sanitized() -> None:
    release = Event()

    class SlowEntryPoint(EntryPoint):
        def load(self) -> object:
            release.wait()
            return object()

    try:
        result = discover_operation_providers(
            entry_point_factory=lambda: (SlowEntryPoint("slow", "unused", "group"),),
            use_cache=False,
            timeout_seconds=0.01,
        )
        assert result.failures[0].code == "provider_timeout"
    finally:
        release.set()


def test_provider_discovery_failure_is_bounded_error() -> None:
    release = Event()

    class SlowEntryPoint(EntryPoint):
        def load(self) -> object:
            release.wait()
            return object()

    try:
        with pytest.raises(ProviderDiscoveryError, match="provider_timeout"):
            result = discover_operation_providers(
                entry_point_factory=lambda: (SlowEntryPoint("slow", "unused", "group"),),
                use_cache=False,
                timeout_seconds=0,
            )
            result.raise_for_failures()
    finally:
        release.set()


def test_default_discovery_is_cached_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import odoo_instance_sdk.operations.provider_loader as loader

    clear_provider_cache()
    calls = 0

    def source(**_: object) -> tuple[EntryPoint, ...]:
        nonlocal calls
        calls += 1
        return ()

    monkeypatch.setattr(loader, "entry_points", source)
    assert discover_operation_providers(timeout_seconds=0.2).providers == ()
    assert discover_operation_providers(timeout_seconds=0.2).providers == ()
    assert calls == 1
    clear_provider_cache()


def test_fixture_provider_is_discovered_exported_and_invocable() -> None:
    entry = EntryPoint(
        "fixture-provider",
        "tests.fixtures.operation_provider.provider",
        "odoo_instance_sdk.operations",
    )
    result = discover_operation_providers(
        entry_point_factory=lambda: (entry,), use_cache=False, timeout_seconds=0.2
    )

    assert not result.failures
    registry = build_registry(providers=cast("Any", result.providers))
    binding = registry.get("fixture.greeting")
    assert binding.descriptor.request_type is not None
    request = binding.descriptor.request_type(name="Ada")
    response = cast("Any", binding.factory)(request, None)
    assert response.greeting == "hello Ada"
    assert response.payload is None
    bundle = cast("Any", registry.bundle())
    assert "FixtureResult" in bundle["schemas"]
    assert "FixtureError" in bundle["schemas"]
