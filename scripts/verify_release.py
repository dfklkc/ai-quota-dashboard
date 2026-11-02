#!/usr/bin/env python3
"""Offline package checks; never reads real user credentials or queries providers."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / '.build.noindex/AI 桌面卡片.app'
DIST = ROOT / 'dist'
VERSION = (ROOT / 'VERSION').read_text().strip()
STEM = 'AIQuotaDashboard-' + VERSION + '-macOS-universal'

def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True, **kwargs).stdout

info = plistlib.loads((APP / 'Contents/Info.plist').read_bytes())
assert info['CFBundleShortVersionString'] == VERSION
assert info['CFBundleIconFile'] == 'AppIcon'
for filename in ['AIDesktopCard', 'ChromeSessionBridge']:
    architectures = run('lipo', '-archs', APP / 'Contents/MacOS' / filename)
    assert 'arm64' in architectures and 'x86_64' in architectures
run('codesign', '--verify', '--deep', '--strict', APP)
resources = APP / 'Contents/Resources'
assert (resources / 'AppIcon.icns').stat().st_size > 10000
config = json.loads((resources / 'accounts.default.json').read_text())
assert all(not a.get('email') and a['follow_current_session'] for a in config['chatgpt_accounts'])
assert config['linksapi']['enabled'] is False
assert config['linksapi']['query_mode'] == 'account'
assert config['linksapi']['credential_source'] == 'manual'
assert not (APP / 'Contents/Resources/tokens').exists()
for path in (ROOT / 'src').iterdir():
    if path.is_file():
        source = path.read_text()
        assert not re.search(r'@(gmail\.com|proton\.me)', source), path
        assert not re.search(r'sk-[A-Za-z0-9_-]{24,}', source), path
        assert '/Users/a1-6' not in source, path
with tempfile.TemporaryDirectory(prefix='quota-clean-') as temp:
    env = dict(os.environ, AI_QUOTA_DATA_DIR=temp)
    env.pop('PYTHONHOME', None); env.pop('PYTHONPATH', None)
    output = run(APP / 'Contents/MacOS/AIDesktopCard', '--self-check', env=env)
    assert json.loads(output)['ok'] is True
    created = json.loads((Path(temp) / 'ai_accounts_config.json').read_text())
    assert created == config
    assert not any((Path(temp) / 'tokens').iterdir())
    user_config = Path(temp) / 'ai_accounts_config.json'
    before = user_config.read_bytes() + b'\n'
    user_config.write_bytes(before)
    run(APP / 'Contents/MacOS/AIDesktopCard', '--self-check', env=env)
    assert user_config.read_bytes() == before, 'Existing configuration must be preserved'
run('codesign', '--verify', '--deep', '--strict', APP)
for line in (DIST / 'SHA256SUMS.txt').read_text().splitlines():
    digest, name = line.split('  ', 1)
    assert hashlib.sha256((DIST / name).read_bytes()).hexdigest() == digest, name
pkg = DIST / (STEM + '.pkg')
raw_files = run('pkgutil', '--payload-files', pkg).splitlines()
assert not any('..' in PurePosixPath(p).parts for p in raw_files)
files = [str(PurePosixPath(p)) for p in raw_files]
assert files and all(p.startswith('Applications/AI 桌面卡片.app') or p in ('.', 'Applications') for p in files)
assert not any('/tokens/' in p or p.endswith('gpt_accounts_quota.json') or p.endswith('chatgpt_web_token.json') for p in files)
with tempfile.TemporaryDirectory(prefix='quota-pkg-') as temp:
    expanded = Path(temp) / 'expanded'
    run('pkgutil', '--expand-full', pkg, expanded)
    installed = next(expanded.rglob('AI 桌面卡片.app'))
    for relative in ['Contents/MacOS/AIDesktopCard', 'Contents/MacOS/ChromeSessionBridge', 'Contents/Resources/AppIcon.icns']:
        assert hashlib.sha256((APP / relative).read_bytes()).digest() == hashlib.sha256((installed / relative).read_bytes()).digest()
    run('codesign', '--verify', '--deep', '--strict', installed)
dmg = DIST / (STEM + '.dmg')
run('hdiutil', 'verify', dmg)
with tempfile.TemporaryDirectory(prefix='quota-dmg-') as temp:
    mount = Path(temp) / 'mounted'; mount.mkdir()
    run('hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', mount, dmg)
    try:
        installed = mount / 'AI 桌面卡片.app'
        assert (mount / 'Applications').is_symlink()
        assert hashlib.sha256((installed / 'Contents/Resources/AppIcon.icns').read_bytes()).digest() == hashlib.sha256((APP / 'Contents/Resources/AppIcon.icns').read_bytes()).digest()
        run('codesign', '--verify', '--deep', '--strict', installed)
    finally:
        run('hdiutil', 'detach', mount)

print('PASS: universal binaries, icon, signatures, clean first launch, bundled runtime, private-data exclusion, PKG payload, DMG and checksums')
