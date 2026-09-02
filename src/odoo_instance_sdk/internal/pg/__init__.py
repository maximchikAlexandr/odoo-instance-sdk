"""PostgreSQL-only transport primitives.

The package intentionally has no eager resource or process imports. Individual
collectors import the narrow module they need when a PostgreSQL operation runs.
"""

from .builder import PsqlSpecification, build_psql_specification, validate_native_psql_args

__all__ = [
    "PsqlSpecification",
    "build_psql_specification",
    "validate_native_psql_args",
]
