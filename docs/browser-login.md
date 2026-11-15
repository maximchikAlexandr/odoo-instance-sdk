# Open Odoo as a selected user

The feature is available only from the authenticated OdCLI monitor panel through its trusted Caddy proxy. The server can impersonate a selected account, so access to this panel and proxy is an operator trust boundary; this flow does not perform that user's password or MFA challenge.

Supported instances are ready local Odoo 19 runtimes with one resolved database and the local filesystem session store. The eligible list contains active internal users only; portal/share users and the Odoo superuser are excluded. Remote instances, other Odoo versions, multiple databases, alternate session stores, and unsupported runtime or publication state fail closed.

The handoff is short lived, one time, held in process memory, and becomes invalid when the monitor restarts. All monitor workers that issue and consume a handoff must therefore be the same process. The session ID and token are not returned by the normal SDK result or written to logs. The SID crosses the local SDK process boundary through a private temporary file that is read once and removed after success or failure.

The browser receives a host-only `Secure`, `HttpOnly`, `SameSite=Lax` Odoo session cookie. Tabs on the same instance host share that cookie; opening another selected user changes the identity in those tabs. Use Odoo's normal logout to invalidate the session. Unsupported cases are reported without starting or reconfiguring the runtime.

A session may be saved immediately before the HTTP response is acknowledged. If the client disconnects in that narrow window, the server cannot know whether the browser received the cookie; the orphaned session then follows Odoo's normal expiry and logout behavior.

Handoff values, SIDs, session tokens, passwords, cookies, private hostnames, and personal data must not be retained in URLs, browser storage, logs, Git, screenshots, or evidence.
