"""Every module must at least import.

Twice now a syntax error reached a file that the running console imports, and
was found by starting the program rather than by the suite. Importing costs
milliseconds and catches the whole class.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import vmd

MODULES = sorted(
    module.name
    for module in pkgutil.walk_packages(vmd.__path__, prefix="vmd.")
    if not module.name.endswith("__main__")  # importing that would start the console
)


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    importlib.import_module(name)


def test_the_entry_points_compile() -> None:
    """__main__ modules are not imported above, so compile them instead."""
    import pathlib
    import py_compile

    root = pathlib.Path(vmd.__file__).parent
    for path in root.rglob("__main__.py"):
        py_compile.compile(str(path), doraise=True)
