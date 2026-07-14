import json
import sys
from pathlib import Path

from getaway import paths, prefs, store
from getaway.constants import EXIT_NEGATIVE


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "incr":
        path = Path(sys.argv[2])
        for _ in range(int(sys.argv[3])):
            paths.atomic_update(path, lambda d: {**d, "n": d.get("n", 0) + 1})
    elif cmd == "set-patch":
        prefs.set_patch(json.loads(sys.stdin.read()))
    elif cmd == "enhance-merge":
        from getaway import enhance

        enhance.merge(sys.argv[2], sys.argv[3], json.loads(sys.stdin.read()))
    elif cmd == "reserve":
        # Reserve one call's quota and exit holding it, so a sibling process sees
        # the reservation. EXIT_NEGATIVE marks a refusal at the floor.
        db = Path(sys.argv[2])
        floor = int(sys.argv[3])
        try:
            store.connect(db).reserve_quota(floor)
        except store.QuotaFloorError:
            raise SystemExit(EXIT_NEGATIVE) from None
    else:
        raise SystemExit(f"unknown runner command {cmd!r}")


if __name__ == "__main__":
    main()
