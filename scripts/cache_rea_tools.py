#!/usr/bin/env python3
"""Capture image-bound MCP metadata at deployment time, without opening samples."""
import argparse
import asyncio
from pathlib import Path
import tempfile

from traceglass.integrations.rea import ReaSessions


async def capture(kit):
    pool = ReaSessions(kit)
    try:
        with tempfile.TemporaryDirectory(prefix='traceglass-schema-') as root:
            count = await pool.refresh_schema(Path(root))
            print(f'Cached {count} REA tool schemas at {kit / "TOOLS.json"}; no sample analyzed.')
    finally:
        await pool.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kit', type=Path, required=True)
    asyncio.run(capture(parser.parse_args().kit.resolve(strict=True)))
