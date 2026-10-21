## 1. Implementation
- [x] 1.1 Capture an independent helper port and separate source/helper endpoint identity.
- [x] 1.2 Route HTTP, readiness and password-request proof to the effective runtime; preserve cleanup and runtime reuse.

## 2. Verification
- [x] 2.1 Verify occupied-primary regression, HTTP routing, lost ownership and port-race rejection, plus existing COPY/adoption/restore tests.
- [x] 2.2 Run lint, typing and change-level OpenSpec validation. At the reviewed fix SHA, focused tests, lint and typing passed; global strict OpenSpec validation passed 44/44, so the earlier 12-error report was not reproducible.
