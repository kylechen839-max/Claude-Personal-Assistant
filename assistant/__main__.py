"""`python -m assistant` (the old name) runs Kwil, the same as `python -m kwil`."""

import sys

from kwil.cli import main

sys.exit(main())
