"""The old name of the `kwil` package, kept for at least one stable release
after the Kwil rebrand (2026-10-22).

PC Task Scheduler tasks (\\HeyClaude\\...), Pi systemd units and the Render
start command run `python -m assistant ...` / `python -m assistant.host`
until they are re-registered, and older scripts import `assistant.X`. Both
keep working: `import assistant.X` returns the very same module object as
`kwil.X` (an alias on sys.meta_path, assistant/_alias.py, not a copy), so
module state, monkeypatch and mock.patch through either name are shared.

Every module that existed before the rename also has a one-file shim here
(assistant/X.py -> kwil.X): a process started on the old code (the old
deploy, watchdog or web app) keeps the old package in memory, and its lazy
`from assistant.X import ...` after `git pull` finds that file. New code uses
`kwil`.
"""

import importlib

import kwil
from assistant._alias import NEW, OLD, install

__version__ = kwil.__version__

install()


def __getattr__(name):
    """assistant.config etc. as attributes, like `import assistant` then
    `assistant.config` worked before (once imported)."""
    if name.startswith("__"):
        raise AttributeError(name)
    try:
        return importlib.import_module(f"{OLD}.{name}")
    except ModuleNotFoundError as e:
        if e.name not in (f"{OLD}.{name}", f"{NEW}.{name}"):
            raise
        raise AttributeError(f"module {OLD!r} has no attribute {name!r}") from None
