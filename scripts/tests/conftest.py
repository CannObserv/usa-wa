"""Make this directory importable so the guards can share `systemd_units` (#167 CR, finding 5).

pytest's default ``importmode=prepend`` already inserts a test file's directory
into ``sys.path``, but that is a default, not a contract — under ``importlib``
mode it does not, and a sibling-module import would break. Doing it explicitly
here keeps the shared parser importable under any import mode.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
