#!/usr/bin/env python3
"""Run native, invisible-window AppKit regressions on macOS."""
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if platform.system() != 'Darwin':
    raise SystemExit('Window presentation checks require macOS')

with tempfile.TemporaryDirectory(prefix='quota-window-tests-') as directory:
    temp = Path(directory)
    main = temp / 'main.swift'
    shutil.copyfile(ROOT / 'tests/window_presentation.swift', main)
    binary = temp / 'window-tests'
    subprocess.run(['swiftc', '-target', platform.machine() + '-apple-macosx14.0',
                    str(main), str(ROOT / 'src/WindowPresentation.swift'), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
