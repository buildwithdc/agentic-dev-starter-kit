"""Main entry point for running the app module directly via `python -m app`."""

import sys
from app.cli import main

if __name__ == "__main__":
    sys.exit(main())
