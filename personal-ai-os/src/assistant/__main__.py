"""So `python3 -m assistant ...` works exactly like the `assistant` command."""
from .cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
