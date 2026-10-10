"""Private Git askpass entrypoint used only by a credential-scoped child."""

from __future__ import annotations

import os
import sys


def main() -> int:
    prompt = " ".join(sys.argv[1:]).casefold()
    key = "GITLAB_LOGIN" if "username" in prompt or "user" in prompt else "GITLAB_TOKEN"
    value = os.environ.get(key)
    if not value:
        return 1
    sys.stdout.write(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
