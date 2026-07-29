from pathlib import Path
import sys


def application_root() -> Path:
    """Return the project root in development or executable folder when packaged."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = application_root()
EXPORT_DIR = APP_ROOT / "data" / "exports"
