#!/usr/bin/env python3
"""Build a standalone universal macOS app, PKG and DMG from allowlisted inputs."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.build.noindex'
DIST = ROOT / 'dist'
CACHE = ROOT / '.cache'
VERSION = (ROOT / 'VERSION').read_text().strip()
APP_NAME = 'AI 桌面卡片.app'
SCRIPTS = ['fetch_quotas.py', 'bind_account.py', 'fetch_linksapi.py']

def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)

def runtime(asset):
    CACHE.mkdir(exist_ok=True)
    archive = CACHE / asset['name']
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != asset['sha256']:
        print('Downloading verified Python runtime:', asset['arch'], flush=True)
        run('gh', 'release', 'download', json.loads((ROOT / 'runtime/manifest.json').read_text())['release'],
            '--repo', 'astral-sh/python-build-standalone', '--pattern', asset['name'], '--dir', CACHE, '--clobber')
    if hashlib.sha256(archive.read_bytes()).hexdigest() != asset['sha256']:
        raise SystemExit('Runtime SHA-256 verification failed: ' + asset['arch'])
    target = RESOURCES / 'runtime' / asset['arch']
    target.mkdir(parents=True)
    run('tar', '-xzf', archive, '-C', target, '--strip-components=1')
    # Keep bytecode caches out of the signed app bundle.
    for directory in list(target.rglob('__pycache__')):
        shutil.rmtree(directory, ignore_errors=True)
    return asset['arch']

if __name__ == '__main__':
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(); DIST.mkdir(exist_ok=True)
    APP = BUILD / APP_NAME
    MACOS = APP / 'Contents/MacOS'
    RESOURCES = APP / 'Contents/Resources'
    MACOS.mkdir(parents=True); RESOURCES.mkdir(parents=True)
    iconset = BUILD / 'AppIcon.iconset'; iconset.mkdir()
    for size in [16, 32, 128, 256, 512]:
        for scale in [1, 2]:
            name = 'icon_%dx%d%s.png' % (size, size, '@2x' if scale == 2 else '')
            run('sips', '-z', size * scale, size * scale, ROOT / 'assets/AppIcon.png', '--out', iconset / name,
                stdout=subprocess.DEVNULL)
    run('iconutil', '-c', 'icns', iconset, '-o', RESOURCES / 'AppIcon.icns')
    shutil.copy2(RESOURCES / 'AppIcon.icns', ROOT / 'assets/AppIcon.icns')
    scripts = RESOURCES / 'scripts'; scripts.mkdir()
    for filename in SCRIPTS:
        shutil.copy2(ROOT / 'src' / filename, scripts / filename)
    shutil.copy2(ROOT / 'config/accounts.default.json', RESOURCES / 'accounts.default.json')
    shutil.copy2(ROOT / 'docs/THIRD_PARTY.txt', RESOURCES / 'THIRD_PARTY.txt')
    manifest = json.loads((ROOT / 'runtime/manifest.json').read_text())
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for arch in pool.map(runtime, manifest['runtimes']):
            print('Bundled Python:', arch, flush=True)
    # main.swift is required for the multi-file Swift executable entry point.
    shutil.copy2(ROOT / 'src/DesktopCard.swift', BUILD / 'main.swift')
    for arch in ['arm64', 'x86_64']:
        target = arch + '-apple-macosx14.0'
        print('Compiling:', arch, flush=True)
        run('swiftc', '-O', '-target', target, '-file-prefix-map', str(ROOT) + '=.',
            BUILD / 'main.swift', ROOT / 'src/AppEnvironment.swift', ROOT / 'src/WindowPresentation.swift', ROOT / 'src/DisplayActivity.swift',
            '-o', BUILD / ('main-' + arch))
        run('clang', '-O2', '-fobjc-arc', '-target', target, '-ffile-prefix-map=' + str(ROOT) + '=.',
            '-framework', 'Cocoa', '-framework', 'ScriptingBridge', ROOT / 'src/ChromeSessionBridge.m',
            '-o', BUILD / ('bridge-' + arch))
    for target, prefix in [('AIDesktopCard', 'main-'), ('ChromeSessionBridge', 'bridge-')]:
        run('lipo', '-create', BUILD / (prefix + 'arm64'), BUILD / (prefix + 'x86_64'), '-output', MACOS / target)
    info = dict(CFBundleDisplayName='AI 桌面卡片', CFBundleName='AI 桌面卡片',
                CFBundleExecutable='AIDesktopCard', CFBundleIdentifier='local.ai.quota-dashboard',
                CFBundlePackageType='APPL', CFBundleShortVersionString=VERSION,
                CFBundleVersion='102', CFBundleIconFile='AppIcon', LSMinimumSystemVersion='14.0', LSUIElement=True,
                NSHighResolutionCapable=True,
                NSAppleEventsUsageDescription='读取 Chrome 当前登录的 ChatGPT 会话，用于本地看板查询对应账号的 Codex 使用额度。')
    (APP / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    run('codesign', '--force', '--deep', '--sign', '-', APP)
    run('codesign', '--verify', '--deep', '--strict', APP)
    print('Building installer packages', flush=True)
    payload = BUILD / 'pkg-root/Applications'; payload.mkdir(parents=True)
    run('ditto', APP, payload / APP_NAME)
    component = BUILD / 'components.plist'
    component.write_bytes(plistlib.dumps([dict(BundleHasStrictIdentifier=True, BundleIsRelocatable=False,
                                             BundleOverwriteAction='upgrade', RootRelativeBundlePath='Applications/' + APP_NAME)]))
    stem = 'AIQuotaDashboard-' + VERSION + '-macOS-universal'
    run('pkgbuild', '--root', BUILD / 'pkg-root', '--component-plist', component,
        '--identifier', 'local.ai.quota-dashboard', '--version', VERSION, '--install-location', '/', DIST / (stem + '.pkg'))
    dmg = BUILD / 'dmg'; dmg.mkdir()
    run('ditto', APP, dmg / APP_NAME)
    (dmg / 'Applications').symlink_to('/Applications')
    shutil.copy2(ROOT / 'docs/INSTALL.txt', dmg / '安装说明.txt')
    run('hdiutil', 'create', '-volname', 'AI 桌面卡片 ' + VERSION, '-srcfolder', dmg, '-ov', '-format', 'UDZO', DIST / (stem + '.dmg'))
    release = dict(version=VERSION, minimum_macos='14.0', architectures=['arm64', 'x86_64'],
                   python_release=manifest['release'], signing='ad-hoc', notarized=False,
                   includes_credentials=False, includes_private_cache=False)
    (DIST / 'release-manifest.json').write_text(json.dumps(release, ensure_ascii=False, indent=2) + '\n')
    files = [DIST / (stem + '.dmg'), DIST / (stem + '.pkg'), DIST / 'release-manifest.json']
    (DIST / 'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in files))
    print('Artifacts:', DIST, flush=True)
