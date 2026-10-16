"""Compatibility shim: the assistant now lives in the `assistant` package.

Use:  python -m assistant --mode {text,speak,voice,wake}
"""

import sys

from assistant.cli import main

if __name__ == "__main__":
    sys.exit(main())
