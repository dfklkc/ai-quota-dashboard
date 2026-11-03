#!/usr/bin/env python3
"""Check publishable Git content without printing any matched private values.

This is a focused publication guard, not a replacement for human review. Existing
icon binaries are approved by their exact digests, including vendor C2PA signing
metadata; other binaries or changed icons require a separate review.
"""
import argparse
from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
APPROVED_BINARY_ASSETS = {
    'assets/AppIcon.png': {'0cdf98a7bd2d1854da8a3afbc6d7ede7cdbc33cc8b30f3defc943f9d47f19d89'},
    'assets/AppIcon.icns': {'e39ccdcf092d57269a1254a8ba1ce6d4a9e0d956fd9468c339495e43d39470c9'},
}
# These exact historical validators contain only an audited negative assertion
# rejecting a local home prefix, rather than a path used to access user files.
# The exception applies only to that rule in history; credentials are still scanned.
APPROVED_HISTORICAL_PATH_ASSERTIONS = {
    'scripts/verify_release.py': {
        '02a653db4cf777524bcbad684e7dec4237f238c58142feff6e41bd480762783f',
        '84d49edf2a1b9bd14412ddabb50f271370999e5e6575c52fa6010c79f1970d70',
    },
}
PRIVATE_FILENAMES = {
    'auth.json', 'ai_accounts_config.json', 'ai_quota_config.json',
    'card_config.json', 'desktop_card_pos.json', 'chatgpt_web_token.json',
    'quota_cache.json', 'gpt_accounts_quota.json', 'chatgpt_web_quota.json',
    'antigravity_quota.json', 'linksapi_quota.json', '.DS_Store',
}
PRIVATE_DIRECTORIES = {'tokens', 'backups', '.swiftbar', '.cache', '.build.noindex', 'dist'}
PATTERNS = {
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'github-token': re.compile(rb'\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}'),
    'api-key': re.compile(rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}'),
    'google-api-key': re.compile(rb'\bAIza[A-Za-z0-9_-]{30,}'),
    'session-jwt': re.compile(rb'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'),
    'literal-credential': re.compile(
        rb'''["']?(?:access_token|api_key|client_secret|password)["']?\s*[:=]\s*["'][A-Za-z0-9+/_=-]{32,}["']''',
        re.IGNORECASE,
    ),
}
EMAIL = re.compile(rb'[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})')
USER_HOME = re.compile(rb'/(?:Users|home)/([A-Za-z0-9_.-]+)(?=/|\b)')
EXAMPLE_DOMAINS = (b'example.com', b'example.org', b'example.net')
EXAMPLE_USERS = {b'example', b'username', b'yourname', b'user', b'runner', b'test'}


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    revision: str = 'working-tree'


def private_path(path):
    parts = PurePosixPath(path).parts
    name = parts[-1] if parts else ''
    return (name in PRIVATE_FILENAMES
            or any(p in PRIVATE_DIRECTORIES or p.endswith('.app') for p in parts)
            or name == '.env' or name.startswith('.env.')
            or PurePosixPath(name).suffix.lower() in {'.pem', '.key', '.p12', '.pfx', '.pkg', '.dmg', '.pyc'})


def scan_bytes(path, data, revision='working-tree'):
    rules = set()
    if private_path(path):
        rules.add('private-runtime-file')
    if path in APPROVED_BINARY_ASSETS:
        if hashlib.sha256(data).hexdigest() not in APPROVED_BINARY_ASSETS[path]:
            rules.add('unreviewed-binary-asset')
        return [Finding(path, rule, revision) for rule in sorted(rules)]
    if b'\0' in data:
        rules.add('unreviewed-binary-file')
    else:
        for rule, pattern in PATTERNS.items():
            if pattern.search(data):
                rules.add(rule)
        for match in EMAIL.finditer(data):
            domain = match[1].lower()
            if not any(domain == allowed or domain.endswith(b'.' + allowed) for allowed in EXAMPLE_DOMAINS):
                rules.add('personal-email')
        reviewed_history_assertion = (revision != 'working-tree' and hashlib.sha256(data).hexdigest()
                                      in APPROVED_HISTORICAL_PATH_ASSERTIONS.get(path, set()))
        if not reviewed_history_assertion and any(m[1].lower() not in EXAMPLE_USERS for m in USER_HOME.finditer(data)):
            rules.add('personal-home-path')
    return [Finding(path, rule, revision) for rule in sorted(rules)]


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)


def check_repository(root, history=False):
    findings = set()
    checked = 0
    # Read tracked working copies too, so unstaged edits to tracked files are checked.
    for encoded in git(root, 'ls-files', '-z').split(b'\0'):
        if not encoded:
            continue
        path = encoded.decode('utf-8')
        file = root / path
        if file.is_symlink():
            data = file.readlink().as_posix().encode('utf-8')
        elif file.is_file():
            data = file.read_bytes()
        else:
            continue
        checked += 1
        findings.update(scan_bytes(path, data))
    if history:
        seen = set()
        for commit in git(root, 'rev-list', '--all').decode().splitlines():
            for record in git(root, 'ls-tree', '-rz', '--full-tree', commit).split(b'\0'):
                if not record:
                    continue
                header, path_raw = record.split(b'\t', 1)
                _, kind, oid = header.split()
                if kind != b'blob':
                    continue
                path = path_raw.decode('utf-8')
                identity = (oid, path)
                if identity in seen:
                    continue
                seen.add(identity)
                checked += 1
                findings.update(scan_bytes(path, git(root, 'cat-file', 'blob', oid.decode()), commit[:12]))
    return sorted(findings, key=lambda x: (x.path, x.rule, x.revision)), checked


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true', help='also check every reachable Git revision')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        findings, checked = check_repository(args.root.resolve(), args.history)
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        print('Publication check could not read the Git content; publishing is blocked.', file=sys.stderr)
        return 2
    for finding in findings:
        # Never echo matched text, credentials, or private source snippets.
        print(f'BLOCK {finding.rule}: {finding.path} [{finding.revision}]')
    if findings:
        return 1
    print(f'PASS: {checked} tracked file versions checked; no publication blockers found')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
