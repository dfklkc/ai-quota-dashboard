import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_content_guard', ROOT / 'scripts/check_public_content.py')
guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)


class PublicContentTests(unittest.TestCase):
    def rules(self, data, path='src/feature.py'):
        return {f.rule for f in guard.scan_bytes(path, data)}

    def test_high_confidence_credentials_are_blocked(self):
        cases = {
            'api-key': b'sk-' + b'a' * 40,
            'github-token': b'ghp_' + b'A' * 40,
            'session-jwt': b'eyJ' + b'a' * 12 + b'.' + b'b' * 20 + b'.' + b'c' * 20,
            'private-key': b'-----BEGIN ' + b'PRIVATE KEY-----',
            'google-api-key': b'AIza' + b'A' * 35,
            'literal-credential': b'api_key = "' + b'A' * 40 + b'"',
        }
        for rule, data in cases.items():
            with self.subTest(rule=rule):
                self.assertIn(rule, self.rules(data))

    def test_example_addresses_and_placeholders_are_allowed(self):
        self.assertEqual(self.rules(b'a@example.com a@sub.example.org /Users/username/project'), set())

    def test_real_account_addresses_and_home_paths_are_blocked(self):
        address = b'private-account@' + b'mail.invalid'
        home = b'/Users/' + b'private-name/project'
        self.assertIn('personal-email', self.rules(address))
        self.assertIn('personal-home-path', self.rules(home))

    def test_runtime_files_are_blocked_even_without_secrets(self):
        for path in ['tokens/session.json', 'auth.json', '.env', 'ai_accounts_config.json',
                     'backups/old/config.json', 'gpt_accounts_quota.json', 'dist/app.pkg']:
            with self.subTest(path=path):
                self.assertIn('private-runtime-file', self.rules(b'{}', path))
        self.assertEqual(self.rules(b'{}', 'config/accounts.default.json'), set())

    def test_approved_icons_are_allowed_but_changed_icons_require_review(self):
        for path in guard.APPROVED_BINARY_ASSETS:
            self.assertEqual(self.rules((ROOT / path).read_bytes(), path), set())
            self.assertIn('unreviewed-binary-asset', self.rules(b'changed', path))

    def test_unknown_binaries_are_not_silently_skipped(self):
        self.assertIn('unreviewed-binary-file', self.rules(b'header\0payload', 'assets/unknown.bin'))

    def test_reviewed_negative_path_assertion_exception_is_exact_and_history_only(self):
        legacy = b"assert '/Users/" + b"private-name' not in source, path\n"
        path = 'scripts/verify_release.py'
        exception = {path: {hashlib.sha256(legacy).hexdigest()}}
        with patch.dict(guard.APPROVED_HISTORICAL_PATH_ASSERTIONS, exception, clear=True):
            self.assertEqual(guard.scan_bytes(path, legacy, 'old-revision'), [])
            self.assertIn('personal-home-path', self.rules(legacy, path))
            self.assertIn('personal-home-path', {f.rule for f in guard.scan_bytes('src/feature.py', legacy, 'old-revision')})
            self.assertIn('personal-home-path', {f.rule for f in guard.scan_bytes(path, legacy + b'changed', 'old-revision')})

    def test_reviewed_path_exception_never_suppresses_credential_checks(self):
        data = b"assert '/Users/" + b"private-name' not in source, path\n" + b'sk-' + b's' * 40
        path = 'scripts/verify_release.py'
        with patch.dict(guard.APPROVED_HISTORICAL_PATH_ASSERTIONS,
                        {path: {hashlib.sha256(data).hexdigest()}}, clear=True):
            self.assertIn('api-key', {f.rule for f in guard.scan_bytes(path, data, 'old-revision')})

    def test_cli_reports_only_location_and_rule(self):
        secret = 'sk-' + 's' * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'sample.py').write_text(secret)
            subprocess.run(['git', '-C', str(root), 'add', 'sample.py'], check=True)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = guard.main(['--root', str(root)])
            self.assertEqual(code, 1)
            self.assertIn('api-key', output.getvalue())
            self.assertIn('sample.py', output.getvalue())
            self.assertNotIn(secret, output.getvalue())

    def test_history_detects_a_secret_removed_from_the_working_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
            git('init', '-q')
            git('config', 'user.name', 'Publication Test')
            git('config', 'user.email', 'test@example.com')
            file = root / 'sample.py'
            file.write_text('sk-' + 's' * 40)
            git('add', 'sample.py'); git('commit', '-qm', 'temporary fixture')
            file.write_text('safe = True\n')
            git('add', 'sample.py'); git('commit', '-qm', 'remove fixture')
            self.assertEqual(guard.check_repository(root)[0], [])
            findings, _ = guard.check_repository(root, history=True)
            self.assertEqual({f.rule for f in findings}, {'api-key'})
            self.assertTrue(all(f.revision != 'working-tree' for f in findings))

    def test_symlink_checks_its_destination_without_reading_external_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'link').symlink_to('/Users/' + 'private-name/auth.json')
            subprocess.run(['git', '-C', str(root), 'add', 'link'], check=True)
            findings, _ = guard.check_repository(root)
            self.assertIn('personal-home-path', {f.rule for f in findings})


if __name__ == '__main__':
    unittest.main()
