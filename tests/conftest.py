from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
CUSTOM_COMPONENTS = ROOT / "custom_components"
TCX_PACKAGE = CUSTOM_COMPONENTS / "tcx_direct"


def _install_namespace(name: str, path: Path) -> None:
    module = ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module


# Import the pure protocol modules without importing Home Assistant's runtime
# integration package. This keeps parser and supervisor-state tests lightweight.
_install_namespace("custom_components", CUSTOM_COMPONENTS)
_install_namespace("custom_components.tcx_direct", TCX_PACKAGE)
