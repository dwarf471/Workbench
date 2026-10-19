"""Read-only application resources and writable user data are separate."""
import os
from pathlib import Path
import sys

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
DATA = Path(os.environ.get('WORKBENCH_DATA_DIR', ROOT / 'data')).expanduser().resolve()
CONFIG_PATH = DATA / 'workbench.local.json' if os.environ.get('WORKBENCH_DATA_DIR') else ROOT / 'workbench.local.json'


def default_mcp_settings_path():
    if sys.platform == 'win32':
        return str(Path(os.environ.get('APPDATA', '')) / 'EpointMsg' / 'setting_rong.json')
    # The macOS client location is not verified; ask the user to select its file.
    return ''
