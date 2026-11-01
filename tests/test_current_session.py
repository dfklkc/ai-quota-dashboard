import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'src'
def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
q = load('current_quotas', 'fetch_quotas.py')
b = load('current_binding', 'bind_account.py')
USAGE = {'rate_limit': {'primary_window': {'used_percent': 23}}}

class CurrentSessionTests(unittest.TestCase):
    def test_app_switch_uses_actual_identity_and_clears_old_quota(self):
        cfg = {'id': 'app', 'type': 'codex_auth', 'email': 'old@example.com', 'auth_file': 'unused', 'follow_current_session': True}
        old = {'email': 'old@example.com', 'last_success_at': '2026-10-24T00:00:00Z', 'p_rem': 90, 's_rem': 80}
        with patch.object(q, 'read_json', return_value={'access_token': 'stub', 'email': 'new@example.com'}), patch.object(q.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(USAGE).encode())):
            result = q.query_account_quota(cfg, old)
        self.assertTrue(result['ok'])
        self.assertEqual(result['email'], 'new@example.com')
        self.assertEqual(result['p_rem'], 77)
        self.assertIsNone(result['s_rem'])

    def test_web_always_reads_browser_even_with_valid_old_token(self):
        cfg = {'id': 'web', 'type': 'token_file', 'follow_current_session': True}
        with patch.object(q, 'sync_chrome_account', return_value={'ok': True, 'email': 'new@example.com', 'usage': USAGE}) as sync, patch.object(q, 'read_json') as read:
            result = q.query_account_quota(cfg, {'email': 'old@example.com', 'p_rem': 90})
        sync.assert_called_once()
        read.assert_not_called()
        self.assertEqual(result['email'], 'new@example.com')
        self.assertEqual(result['p_rem'], 77)

    def test_closed_browser_does_not_reuse_previous_account(self):
        cfg = {'id': 'web', 'type': 'token_file', 'follow_current_session': True}
        with patch.object(q, 'sync_chrome_account', return_value=b.result('chrome_closed')):
            result = q.query_account_quota(cfg, {'email': 'old@example.com', 'last_success_at': '2026-10-24T00:00:00Z', 'p_rem': 90})
        self.assertFalse(result['ok'])
        self.assertEqual(result['email'], '')
        self.assertIsNone(result['p_rem'])
        self.assertNotIn('last_success_at', result)

    def test_web_follow_does_not_require_configured_email(self):
        session = {'email': 'current@example.com', 'access_token': 'stub'}
        with tempfile.TemporaryDirectory() as temp, patch.object(b, 'ROOT', Path(temp)), patch.object(b, 'get_chrome_sessions', return_value=([session], '')), patch.object(b.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(USAGE).encode())):
            result = b.sync_account({'type': 'token_file', 'follow_current_session': True, 'auth_file': str(Path(temp) / 'token.json')})
        self.assertTrue(result['ok'])
        self.assertEqual(result['email'], session['email'])
        self.assertNotIn('credentials', result)

if __name__ == '__main__':
    unittest.main()
