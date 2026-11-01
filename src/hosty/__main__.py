"""Allow running Hosty from a source checkout with ``python -m hosty``."""

import sys

from hosty.main import main

if __name__ == "__main__":
    sys.exit(main())
