"""Copy legacy analysis inputs into the native workspace, never overwriting files."""
import json
from pathlib import Path
from traceglass.assets import import_legacy_assets
from traceglass.config import Settings


if __name__ == '__main__':
    print(json.dumps(import_legacy_assets(Settings.from_env())))
