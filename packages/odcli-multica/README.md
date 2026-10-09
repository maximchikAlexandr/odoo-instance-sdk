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
