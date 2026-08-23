# add-postgres-cluster-lifecycle

Project-level PostgreSQL cluster configuration and lifecycle management for odcli and the Python SDK (issue #8).

Compose startup is fail-closed: after `init`, resolve and explicitly approve the OCI digest, then run `up`.
