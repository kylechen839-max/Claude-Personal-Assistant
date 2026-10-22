"""Compatibility shim: the assistant now lives in the `kwil` package.

Use:  python -m kwil --mode {text,speak,voice,wake}
"""

import sys

from kwil.cli import main

if __name__ == "__main__":
    sys.exit(main())
