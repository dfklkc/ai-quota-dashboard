#!/usr/bin/env python3
"""Exercise production view-model logic without opening a window or reading real accounts."""
import os
from pathlib import Path
import platform
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if platform.system() != 'Darwin':
    raise SystemExit('Display activity checks require macOS')
source = (ROOT / 'src/DesktopCard.swift').read_text()
models = source[:source.index('class DraggableWindow:')]
configuration = source[source.index('class ConfigState:'):source.index('// MARK: - Dedicated Frontend Config Root View')]
with tempfile.TemporaryDirectory(prefix='quota-display-tests-') as directory:
    temp = Path(directory)
    main = temp / 'main.swift'
    main.write_text(models + '\n' + configuration + '\n' + (ROOT / 'tests/display_activity.swift').read_text())
    binary = temp / 'display-tests'
    subprocess.run(['swiftc', '-target', platform.machine() + '-apple-macosx14.0', str(main),
                    str(ROOT / 'src/AppEnvironment.swift'), str(ROOT / 'src/DisplayActivity.swift'),
                    '-o', str(binary)], check=True)
    subprocess.run([str(binary)], env=dict(os.environ, AI_QUOTA_DATA_DIR=str(temp / 'data')), check=True)
