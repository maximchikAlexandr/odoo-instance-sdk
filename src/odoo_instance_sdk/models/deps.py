from __future__ import annotations

import msgspec


class DepsDistributionDetail(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One pip-check distribution line."""

    detail: str


class DepsMissingImport(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One missing addon import discovered during dependency verification."""

    module: str
    import_name: str


class DepsVerifyResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Outcome of Python and add-on dependency verification."""

    distributions: tuple[DepsDistributionDetail, ...] = ()
    missing_imports: tuple[DepsMissingImport, ...] = ()
    pip_check_ok: bool = True
    pip_check_output: str = ""

    @property
    def ok(self) -> bool:
        """Return the single dependency-verification success predicate."""
        return self.pip_check_ok and not self.missing_imports

    def to_json_payload(
        self,
    ) -> dict[str, str | bool | list[dict[str, str]]]:
        """Project the public result into CLI-safe JSON fields."""
        return {
            "distributions": [{"detail": item.detail} for item in self.distributions],
            "missing_imports": [
                {"module": item.module, "import": item.import_name} for item in self.missing_imports
            ],
            "pip_check_ok": self.pip_check_ok,
            "pip_check_output": self.pip_check_output,
        }
