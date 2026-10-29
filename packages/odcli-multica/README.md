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

Git and GitLab operations use the resolved human root creator by default.
Credential-dependent `git`, `git sync`, and `gitlab mr publish` also accept
`--member-id USER_ID` to select a different current human in the verified
workspace. The selection is checked again immediately before credentials are
delivered or publication begins. It changes only the credential identity; the
verified checkout, issue, workspace, repository, origin, branch, and host stay
authoritative. A missing selected credential fails without trying another
member. Remote URLs, argv, logs, and result documents contain only safe
host/project and selected-member/source observations; credentials are supplied
to the child process for one command and redacted at the boundary. Publication
descriptions are file-backed and bounded.

The bridge resolves credentials through the core encrypted-first resolver and
the effective user root (`ODCLI_USER_ROOT`, or `~/.odcli`). It does not infer
`.odcli` mapping or dotenv paths from the project directory. Process environment
and explicitly supplied legacy mapping paths remain supported; project-local
credential files are used only when their paths are passed explicitly.

## Native shared-cache reads

The SDK installs `git-credential-odcli`, a thin alias for the existing finite
Git helper. A daemon generation can use it for native Git cache reads by
inheriting the exact cache member, HTTPS host, and purpose in its launch
environment, with Git configuration kept process-local:

```sh
ODCLI_GITLAB_PURPOSE=cache-read \
ODCLI_GITLAB_MEMBER_ID="$CACHE_MEMBER_USER_ID" \
ODCLI_GITLAB_SELECTED_HOST=gitlab.example \
GIT_CONFIG_COUNT=2 \
GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0= \
GIT_CONFIG_KEY_1=credential.helper GIT_CONFIG_VALUE_1=odcli \
multica daemon start
```

The helper requires `get`, HTTPS, and the configured exact host. Cache reads
resolve only that member and host, verify that the configured login matches
`/api/v4/user`, then require `/api/v4/personal_access_tokens/self` to report the
same provider user, `active: true`, and exactly the standard `read_repository`
scope. Broader, granular, unavailable, or mismatched token information fails
closed without fallback. No provider ID or token is persisted by this check.

Treat each daemon generation's inherited environment as immutable. To replace
the cache identity, stop that profile and its Git children, validate the
candidate through the installed helper with `git credential fill` redirected
to `/dev/null`, then start the next generation with the replacement
environment and check its status. Rollback uses the same stop → validate →
start → status sequence with the prior environment. Do not write the helper or
member into global/repository Git configuration; task operations continue to
use their selected member's one-command credential snapshot.

The independent C1–C4 path uses
`python scripts/e2e_gitlab_member_workflow.py --mode multica-cache` and requires a pre-authenticated, isolated Multica profile
that is stopped, two clean task checkouts for the same private test repository,
current member A/B fixtures, and a third current human member with no matching
GitLab credential. Put fixture tokens only in owner-only user-root storage and
install the candidate SDK into the helper directory. The runner validates the
helper through Git, cold-clones the repository, checks automatic and explicit
member observations, proves the missing-credential operation fails, starts a
replacement daemon generation, then restarts with the runner's prior process
environment and checks that replacement inputs are absent. It stops the test
daemon at cleanup; remove the disposable profile and local cache using the
profile-specific setup's cleanup procedure.

For `gitlab mr publish`, pass `--issue-url` with the exact HTTPS issue link when
the Multica client profile has no `server_url`. Git operations use the verified
checkout root and an installed `odcli-multica-askpass` executable for HTTPS.

## Merge request assignee

`gitlab mr publish` reads the optional catalog setting
`gitlab.mr.default_assignee` as a global fallback and
`gitlab.mr.default_assignee:https://<normalized-host>/<normalized-project-path>`
for an exact normalized GitLab host and project. Either JSON value is a
positive numeric GitLab user ID or exact username. A nonblank `--assignee`
value wins first, then the project-specific setting, then the global setting;
otherwise publication leaves the assignee unset. A present invalid setting
fails without falling through. Before creating or updating a merge request, the
command verifies the selected user is active and is a member of that project.
It sends `assignee_id`; this does not set or describe a reviewer. Results
report the assignee ID, username, and whether it came from the explicit
override or a catalog default.

## Publication and external panel prerequisites

Core `odcli publish` and `odcli unpublish` operate on the one OdCLI-owned Caddy
route file. A ready project or environment, the configured Caddy binary, and
the local publication settings are required. Validation or reload failure
restores the prior route bytes; unpublish is idempotent. Never edit the owned
route file by hand while a command is running.

The monitor panel is local by default. Use `odcli monitor --external --settings PATH`
for explicit publication mode; startup registers the monitor endpoint in the
owned Caddy route and supplies exact allowed hosts and trusted proxy peers. The
panel accepts forwarded origin headers only from those peers, uses secure CSRF
cookies, and does not expose pgAdmin externally. If a route is missing, stale,
stopped, or unavailable, the panel shows the local endpoint and disables `Open Odoo`.

## Recovery

If publication fails, inspect the sanitized command result and retry after the
runtime is ready and Caddy's configuration is valid. A failed reload preserves
the previous owned route. If a checkout or environment removal cannot remove
its route, fix the Caddy prerequisite and retry removal; do not delete the
checkout to bypass the cleanup gate. Missing GitLab credentials, project
permissions, or network access are reported before any remote mutation.
