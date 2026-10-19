#!/usr/bin/env python3
"""Print effective Skill sources, hashes and optional workspace drift without writes."""
import argparse
import json
from pathlib import Path

from traceglass.config import Settings
from traceglass.integrations.skill_inventory import inspect_skills


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, help='Host workspace root containing skills/')
    args = parser.parse_args()
    result = inspect_skills(Settings.from_env(), workspace_root=args.workspace)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
