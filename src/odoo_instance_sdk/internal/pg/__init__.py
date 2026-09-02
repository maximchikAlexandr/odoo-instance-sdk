"""PostgreSQL-only transport primitives.

The package intentionally has no eager resource or process imports. Individual
collectors import the narrow module they need when a PostgreSQL operation runs.
"""

from .builder import PsqlSpecification, build_psql_specification, validate_native_psql_args
from .context import DatabaseContext, resolve_database_context, resolve_database_name

__all__ = [
    "DatabaseContext",
    "PsqlSpecification",
    "build_psql_specification",
    "resolve_database_context",
    "resolve_database_name",
    "validate_native_psql_args",
]
