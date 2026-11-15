# Spec Delta

## MODIFIED Requirements

### Requirement: React consumes only generated API contracts

React MUST remove handwritten API interfaces/types and endpoint-specific `fetch()` wrappers from `web/src/api.ts`. The only handwritten runtime client module MUST configure the generated Fetch client's relative `baseUrl` and attach the readable CSRF cookie as `X-CSRF-Token` on POST requests; UI polling, errors, runtime actions, and any enabled pgAdmin actions MUST invoke generated operations and use generated types. React MUST NOT reimplement Python eligibility, ownership, cluster-health, nullability, or enum invariants.

#### Scenario: No handwritten API model copy remains

- **WHEN** frontend sources are inspected after generation
- **THEN** snapshot, runtime action, pgAdmin, and HTTP error types are imported from `web/src/generated/` and no equivalent handwritten interfaces or direct endpoint-specific `fetch()` call remains

#### Scenario: Environment card uses typed eligibility

- **WHEN** an environment card renders
- **THEN** its Open pgAdmin button enabled state and explanation come from the generated `pgadmin.state` field, and clicking an eligible button invokes generated `openPgAdmin` and opens the returned loopback URL without putting credentials in the browser address

#### Scenario: pgAdmin dashboard action is deferred

- **WHEN** an environment card renders after automatic master-password provisioning is part of the open operation
- **THEN** the card does not hide Open pgAdmin; the button stays visible and its enabled state comes from `pgadmin.state`

#### Scenario: Ineligible pgAdmin action stays visible and disabled

- **WHEN** an environment card renders with a `pgadmin.state` other than `eligible`
- **THEN** Open pgAdmin is visible, disabled, and explains that state without calling `openPgAdmin`
