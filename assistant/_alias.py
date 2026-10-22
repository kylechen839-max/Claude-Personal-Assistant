"""The `assistant` -> `kwil` alias (see assistant/__init__.py).

A finder on sys.meta_path: `import assistant.X` returns the `kwil.X` module
object itself, and `python -m assistant.X` runs kwil/X.py (runpy asks the
loader for the code). The one-file shims next to this file install it too:
a process started before the rename has the old `assistant` package in
memory (without this finder) and finds them on disk after `git pull`.
"""

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys

OLD, NEW = "assistant", "kwil"


def real_name(fullname: str) -> str:
    return NEW + fullname[len(OLD):]


class Alias(importlib.abc.MetaPathFinder, importlib.abc.InspectLoader):
    """assistant.X -> the kwil.X module itself."""

    def __init__(self):
        self._real_specs = {}

    # --- finder ---
    def find_spec(self, fullname, path=None, target=None):
        # assistant.__main__ and assistant._alias are this folder's own files
        if not fullname.startswith(OLD + ".") or fullname in (OLD + ".__main__", __name__):
            return None
        real = importlib.util.find_spec(real_name(fullname))
        if real is None:
            return None  # no such module under the new name either
        spec = importlib.machinery.ModuleSpec(
            fullname, self, origin=real.origin,
            is_package=real.submodule_search_locations is not None)
        spec.has_location = real.has_location  # runpy sets __file__ from it
        return spec

    # --- loader: import returns the real module ---
    def create_module(self, spec):
        module = importlib.import_module(real_name(spec.name))
        self._real_specs[spec.name] = module.__spec__
        return module

    def exec_module(self, module):
        # The import system has just pointed __spec__ at the alias: give the
        # module its real one back (reload and relative imports read it).
        real = self._real_specs.pop(module.__spec__.name, None)
        if real is not None:
            module.__spec__ = real

    # --- loader: what runpy (python -m assistant.X) asks for ---
    def _real_loader(self, fullname):
        real = importlib.util.find_spec(real_name(fullname))
        if real is None:
            raise ImportError(f"no module named {real_name(fullname)!r}", name=fullname)
        return real.loader, real.name

    def get_code(self, fullname):
        loader, name = self._real_loader(fullname)
        return loader.get_code(name)

    def get_source(self, fullname):
        loader, name = self._real_loader(fullname)
        return loader.get_source(name)

    def is_package(self, fullname):
        loader, name = self._real_loader(fullname)
        return loader.is_package(name)

    def get_filename(self, fullname):
        loader, name = self._real_loader(fullname)
        return loader.get_filename(name)


def install() -> None:
    """Put the alias first on sys.meta_path, once per process."""
    if not any(type(f).__qualname__ == "Alias" and type(f).__module__ == __name__
               for f in sys.meta_path):
        sys.meta_path.insert(0, Alias())


def shim(name: str) -> None:
    """For a one-file shim `assistant/X.py`: become the `kwil.X` module."""
    install()
    sys.modules[name] = importlib.import_module(real_name(name))
