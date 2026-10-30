"""The dotted module a source file defines (#553).

Not a test module. ``test_declared_dependencies`` (which module makes each import) and
``test_import_contracts`` (which adapter modules are transports) both turn a file under a
source root into a module name, and import the rule from here so the two scans can never
disagree about what counts as a module. ``conftest.py`` in this directory puts it on
``sys.path`` regardless of mode.
"""

from pathlib import Path


def module_name(root: Path, path: Path) -> str:
    """The dotted module a file under ``root`` defines (``pkg/__init__.py`` → ``pkg``)."""
    parts = path.relative_to(root).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
