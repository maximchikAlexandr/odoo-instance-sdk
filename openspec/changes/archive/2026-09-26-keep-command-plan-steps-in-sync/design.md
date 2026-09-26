## Context

The public `ExecutionPlan.steps` and the private `PreparedCommand.steps` are supplied separately to `Command.from_prepared()`. The private snapshot already provides a deterministic, secret-free public projection for every `PreparedStep` and `PreparedAction`, but the shared constructor currently fingerprints and stores the supplied plan without comparing those sequences. Most production builders project the same captured tuple into both objects; detached launch instead projects only its preparation/process tuple while privately appending four lifecycle actions.

The repository contract requires one immutable inspectable snapshot for preview and execution, frozen JSON-safe public plans, side-effect-free planning, and no parallel runner or command-specific synchronization mechanism. The implementation must preserve plan observations, warnings, redaction, fingerprints, repeatable runs, and the existing per-run consumption ledger.

## Goals / Non-Goals

**Goals:**

- Make exact ordered public/private step parity a construction-time invariant for every `Command`.
- Fail before command registration or execution when either side omits, adds, reorders, or changes a projected step.
- Make detached launch expose its four existing lifecycle actions without performing them during construction or dry-run.
- Keep the change small and reuse existing projections and exception types.

**Non-Goals:**

- Reorder or redesign execution effects, the `RunContext` ledger, process execution, or detached lifecycle behavior.
- Derive observations, warnings, semantic summaries, or caller-specific metadata from private steps.
- Change public method signatures, serialization formats, persistence schemas, dependencies, or compatibility for already-valid commands.
- Add command-specific parity branches or a second snapshot abstraction.

## Decisions

### Validate the complete step projection in `Command.from_prepared()`

The constructor will compute `tuple(step.public_projection() for step in prepared.steps)` and compare it directly with `plan.steps` before fingerprinting the plan, constructing the public command, or inserting its private snapshot into `_COMMANDS`. Any difference raises `PlanValidationError` with a stable diagnostic that identifies plan/snapshot parity as the failure.

Full structural equality is intentional: comparing only identifiers would miss reordered steps, process/action substitutions, changed flags, redacted argv, or other fields that make dry-run describe a different operation. Reusing each prepared step's existing projection keeps redaction and public-shape logic centralized.

Alternative considered: derive a replacement `ExecutionPlan.steps` inside the constructor. Rejected because the caller remains the owner of observations, warnings, semantic intent, and fingerprint inputs; silently replacing only one field would conceal broken builders and could invalidate a precomputed fingerprint. Explicit rejection makes defects visible at their source.

### Build detached public and private inputs from one local tuple

Detached launch will combine its existing preparation/process steps and four existing lifecycle actions into one immutable tuple, then use that tuple for both `_command_plan()` and `Command.create()`. The actions retain their current identifiers, descriptions, read-only/mutating classifications, and callback behavior. No action is executed while the tuple or plan is built.

Alternative considered: special-case detached launch in the central constructor. Rejected because it would weaken the universal invariant and duplicate caller knowledge at the shared boundary.

### Treat invalid test doubles like invalid production builders

Focused construction tests will cover private-only, public-only, same-identifier field/order mismatches, and the valid path. Existing tests that create a non-empty public plan with an empty private snapshot solely as a display double will be updated to supply matching prepared steps; empty-plan/empty-snapshot doubles remain valid. This keeps tests subject to the same public API contract as production.

## Risks / Trade-offs

- **Latent mismatches fail during construction** → This is the required fail-closed behavior; update any exposed production builder or test double to project one captured tuple rather than weakening validation.
- **A detailed exception could leak private inputs** → Use a generic parity diagnostic and do not include private step values, argv, environment, stdin, or secrets.
- **Fingerprinting a mismatched plan could create misleading evidence** → Validate before generating a missing fingerprint or registering the command.
- **Validation repeats public projection work** → The tuple sizes are bounded and already projected by builders; the small construction cost is preferable to maintaining parallel metadata or cached projections.

## Migration Plan

No data or deployment migration is required. Implement central validation first, update detached launch and any invalid test doubles revealed by focused/full verification, then run the repository's formatting, lint, type, architecture, and test gates. Rollback is the single implementation commit; persisted state and public signatures are unchanged.

## Open Questions

None. GitHub #95 explicitly permits rejection rather than derivation, and rejection preserves caller-owned plan metadata while enforcing the invariant centrally.
