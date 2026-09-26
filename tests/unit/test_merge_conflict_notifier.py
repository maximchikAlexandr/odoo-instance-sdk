from __future__ import annotations

import importlib.util
from email.message import Message
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/notify_merge_conflicts.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("notify_merge_conflicts", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_conflicts_are_retried_and_reported_with_exact_refs() -> None:
    notifier = _module()
    calls: dict[str, int] = {}
    delays: list[float] = []

    def get(path: str) -> object:
        calls[path] = calls.get(path, 0) + 1
        if path.startswith("/pulls?"):
            return [{"number": 7}, {"number": 8}]
        if path == "/pulls/7" and calls[path] == 1:
            return {"mergeable": None}
        if path == "/pulls/7":
            return {
                "number": 7,
                "mergeable": False,
                "html_url": "https://github.com/example/repo/pull/7",
                "title": "conflict",
                "draft": False,
                "head": {"ref": "feat/conflict", "sha": "head-7"},
                "base": {"ref": "main", "sha": "base-1"},
            }
        return {"number": 8, "mergeable": True}

    conflicts = notifier.conflicting_pull_requests(
        get, "main", attempts=2, retry_delay=3, sleep=delays.append
    )

    assert conflicts == [
        {
            "number": 7,
            "url": "https://github.com/example/repo/pull/7",
            "title": "conflict",
            "draft": False,
            "head_branch": "feat/conflict",
            "head_sha": "head-7",
            "base_branch": "main",
            "base_sha": "base-1",
        }
    ]
    assert calls["/pulls/7"] == 2
    assert delays == [3]


def test_mergeability_retries_use_capped_exponential_backoff() -> None:
    notifier = _module()
    detail_calls = 0
    delays: list[float] = []

    def get(path: str) -> object:
        nonlocal detail_calls
        if path.startswith("/pulls?"):
            return [{"number": 7}]
        detail_calls += 1
        return {"number": 7, "mergeable": None if detail_calls < 5 else True}

    assert (
        notifier.conflicting_pull_requests(
            get, "main", attempts=5, retry_delay=10, sleep=delays.append
        )
        == []
    )
    assert delays == [10, 20, 30, 30]


def test_delivery_errors_never_expose_the_secret_url(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier = _module()
    secret_url = "https://example.invalid/webhook/super-secret-token"

    def fail(request: object, timeout: int) -> object:
        del request, timeout
        raise HTTPError(secret_url, 500, "failed", hdrs=Message(), fp=None)

    monkeypatch.setattr(notifier, "urlopen", fail)

    with pytest.raises(RuntimeError, match="HTTP 500") as raised:
        notifier.deliver(secret_url, {"event": "test"}, "key")

    assert secret_url not in str(raised.value)
