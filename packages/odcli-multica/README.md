# odcli-multica

`odcli-multica` is a small, stateless bridge between a native Multica task
checkout and the public Odoo Instance SDK adoption API.

The workflow has two explicit phases:

1. `MulticaOdooClient.checkout()` uses typed `multica-py` repository operations
   and keeps native checkout ownership with Multica.
2. `odcli-multica context PATH` verifies the explicit issue/run/project and
   same-host filesystem evidence. `odcli-multica env prepare PATH` then captures
   and delegates the core `EnvironmentResource.adopt_command()` COPY plan.

The caller retains the frozen context and resulting environment UUID. This
package does not create project links, bind/unbind records, task mutations,
background workers, or a second persistence layer. Preparation does not start
Odoo.

The extension consumes the existing bounded core output contract:
`OutputMode`, `OutputDocument`, `success_document`, `failure_document`,
`model_to_dict`, and `emit` from `odoo_instance_sdk.commands.output`. Rich,
JSON, and TOON therefore share one sanitized envelope and machine output is
one document.

The dependency on `multica-py` is pinned to the verified public typed contract
at revision `c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed`; the native CLI floor is
`0.5.3`. The core dependency is pinned to the revision containing the caller-
owned adoption API.

## Independent workflow leaves

After the verified task context is available, the extension's leaves are
independent: root-creator credential resolution, ordered module context, native
Git passthrough, HTTPS GitLab synchronization, and exact-key merge-request
publication each fail before remote work when their prerequisite is absent.
The extension never stores a token or creates a second checkout binding.

Git and GitLab operations run as the resolved human root creator. Remote URLs,
argv, logs, and result documents contain only safe host/project identifiers;
credentials are supplied to the child process for one command and redacted at
the boundary. Publication descriptions are file-backed and bounded.

## Publication and external panel prerequisites

Core `odcli publish` and `odcli unpublish` operate on the one OdCLI-owned Caddy
route file. A ready project or environment, the configured Caddy binary, and
the local publication settings are required. Validation or reload failure
restores the prior route bytes; unpublish is idempotent. Never edit the owned
route file by hand while a command is running.

The monitor panel is local by default. External deployment requires exact
allowed hosts and exact trusted proxy peers. The panel accepts forwarded origin
headers only from those peers, uses secure CSRF cookies, and does not expose
pgAdmin externally. If a route is missing, stale, stopped, or unavailable, the
panel shows the local endpoint and disables `Open Odoo`.

## Recovery

If publication fails, inspect the sanitized command result and retry after the
runtime is ready and Caddy's configuration is valid. A failed reload preserves
the previous owned route. If a checkout or environment removal cannot remove
its route, fix the Caddy prerequisite and retry removal; do not delete the
checkout to bypass the cleanup gate. Missing GitLab credentials, project
permissions, or network access are reported before any remote mutation.
