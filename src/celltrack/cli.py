from __future__ import annotations

import sys

from celltrack.pipelines import segmentation, tracking


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Usage: celltrack {segment|track} ...", file=sys.stderr)
        return 2
    command, command_args = args[0], args[1:]
    if command == "segment":
        return segmentation.main(command_args)
    if command == "track":
        return tracking.main(command_args)
    print(f"Unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
