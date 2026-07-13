## Commit Messages

Use Conventional Commits for all repository commits:

```text
<type>[optional scope]: <description>
```

Allowed types: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`, `ci`,
`chore`, `style`, `revert`.

The repository enforces this with `.githooks/commit-msg`. Enable it locally with:

```sh
git config core.hooksPath .githooks
```
