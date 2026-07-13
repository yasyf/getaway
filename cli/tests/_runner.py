import json
import sys
from pathlib import Path

from getaway import paths, prefs


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "incr":
        path = Path(sys.argv[2])
        for _ in range(int(sys.argv[3])):
            paths.atomic_update(path, lambda d: {**d, "n": d.get("n", 0) + 1})
    elif cmd == "set-patch":
        prefs.set_patch(json.loads(sys.stdin.read()))
    else:
        raise SystemExit(f"unknown runner command {cmd!r}")


if __name__ == "__main__":
    main()
