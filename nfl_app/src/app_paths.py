from pathlib import Path
import sys


def application_root() -> Path:
    """Return the project root in development or executable folder when packaged."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """Root for read-only files shipped with the app (templates, config).

    PyInstaller unpacks --add-data into _MEIPASS, which is the _internal folder
    beside the executable in onedir builds — not the executable folder itself, so
    these cannot be looked up under APP_ROOT.
    """
    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled) if bundled else APP_ROOT


APP_ROOT = application_root()
RESOURCE_ROOT = resource_root()
EXPORT_DIR = APP_ROOT / "data" / "exports"
