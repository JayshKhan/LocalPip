"""Allow `python -m localpip` to invoke the CLI."""

from localpip.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
