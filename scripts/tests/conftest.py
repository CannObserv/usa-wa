"""Make this directory importable so the guards can share `systemd_units` (#167 CR, finding 5).

pytest's default ``importmode=prepend`` already inserts a test file's directory
into ``sys.path``, but that is a default, not a contract — under ``importlib``
mode it does not, and a sibling-module import would break. Doing it explicitly
here keeps the shared parser importable under any import mode.

Appended, not prepended (#167 CR round 2, finding 11): importability is all that
is needed, and the front of ``sys.path`` outranks the stdlib for the whole
session — a future helper here named after a stdlib module would shadow it
repo-wide, and the breakage would surface far from this directory.
"""

import sys
from pathlib import Path

HERE = str(Path(__file__).parent)
if HERE not in sys.path:
    sys.path.append(HERE)
