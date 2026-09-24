## 1. Checkout dependency command

- [x] 1.1 In `_checkout_steps()`, derive one dependency Python executable from the resolved checkout plan for both reused and owned modes.
- [x] 1.2 Pass that exact executable through `--python` to checkout's `uv pip compile` and reuse it unchanged in the following install/sync argv, without altering hash-lock behavior.

## 2. Regression coverage

- [x] 2.1 Add a parameterized unit test covering reused and owned checkout modes that proves compile and install/sync receive the same `--python` value and retain their mode-specific operation.
- [x] 2.2 Run the focused environment-Python tests and confirm hash-lock checkout still bypasses compilation.

## 3. Repository verification

- [x] 3.1 Run repository formatting/lint and strict type checks for the completed implementation.
