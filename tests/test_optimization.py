import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'src'
def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
q = load('optimized_quotas', 'fetch_quotas.py')
b = load('optimized_binding', 'bind_account.py')

class OptimizationTests(unittest.TestCase):
    def test_timeout_preserves_completed_browser_session(self):
        output = json.dumps({'email': 'active@example.com', 'access_token': 'stub'}) + '\n'
        with patch.object(b.subprocess, 'run', side_effect=subprocess.TimeoutExpired('stub', 11, output=output.encode())):
            sessions, error = b.get_chrome_sessions()
        self.assertEqual(sessions[0]['email'], 'active@example.com')
        self.assertEqual(error, 'timeout')

    def test_fresh_browser_request_key_every_time(self):
        first = b.session_scripts(); second = b.session_scripts()
        self.assertNotEqual(first, second)
        self.assertIn('10000', first[0])
        self.assertNotIn('if(window[k])', first[0])

    def test_backoff_caps_and_recovers(self):
        with patch.object(q.time, 'time', return_value=1000):
            prior = {}
            delays = []
            for _ in range(5):
                prior = q.record_attempt({'ok': False}, prior)
                delays.append(prior['next_retry_at'] - 1000)
            recovered = q.record_attempt({'ok': True}, prior)
        self.assertEqual(delays, [60, 120, 240, 300, 300])
        self.assertEqual(recovered['failure_count'], 0)
        self.assertEqual(recovered['next_retry_at'], 0)

    def test_connected_without_windows_is_not_synchronized(self):
        result = q.parse_agy_summary({'groups': []}, {})
        self.assertFalse(result['ok'])
        self.assertTrue(result['is_running'])
        self.assertNotIn('last_success_at', result)

    def test_completed_card_saved_before_slow_source_finishes(self):
        gate = threading.Event()
        writes = []
        def query(cfg, old):
            if cfg['id'] == 'slow':
                if not gate.wait(2):
                    raise RuntimeError('fast card was not published')
            return {'id': cfg['id'], 'ok': True}
        def save(path, values):
            writes.append([v['id'] for v in values])
            if any(v['id'] == 'fast' for v in values): gate.set()
        with patch.object(q, 'read_json', return_value=[]), patch.object(q, 'query_account_quota', side_effect=query), patch.object(q, 'atomic_json', side_effect=save):
            q.fetch_all_gpt_accounts({'chatgpt_accounts': [{'id': 'slow'}, {'id': 'fast'}]})
        self.assertEqual(writes[0], ['fast'])
        self.assertEqual(writes[-1], ['slow', 'fast'])

    def test_one_source_exception_does_not_erase_other_results(self):
        def query(cfg, old):
            if cfg['id'] == 'broken': raise ValueError('bad schema')
            return {'id': cfg['id'], 'ok': True}
        with tempfile.TemporaryDirectory() as temp, patch.object(q, 'ROOT', Path(temp)), patch.object(q, 'GPT_ACCOUNTS_CACHE', Path(temp) / 'cache.json'), patch.object(q, 'query_account_quota', side_effect=query):
            q.fetch_all_gpt_accounts({'chatgpt_accounts': [{'id': 'broken'}, {'id': 'working'}]})
            result = json.loads(q.GPT_ACCOUNTS_CACHE.read_text())
        self.assertFalse(result[0]['ok']); self.assertTrue(result[1]['ok'])

    def test_manual_refresh_bypasses_backoff_and_targets_one_card(self):
        cached = [{'id': 'app', 'ok': False, 'next_retry_at': time.time() + 300}, {'id': 'web', 'ok': True}]
        cfg = {'chatgpt_accounts': [{'id': 'app'}, {'id': 'web'}]}
        with patch.object(q, 'read_json', return_value=cached), patch.object(q, 'atomic_json'), patch.object(q, 'query_account_quota', return_value={'id': 'app', 'ok': True}) as request:
            q.fetch_all_gpt_accounts(cfg, source='app')
            request.assert_not_called()
            q.fetch_all_gpt_accounts(cfg, source='app', force=True)
            request.assert_called_once()

    def test_follow_mode_does_not_persist_browser_credential(self):
        session = {'email': 'active@example.com', 'access_token': 'stub', 'source_label': 'Chrome 窗口 1 · 标签 2（活动）'}
        checked = {'ok': True, 'credentials': {'access_token': 'stub'}, 'usage': {}}
        with tempfile.TemporaryDirectory() as temp, patch.object(b, 'ROOT', Path(temp)), patch.object(b, 'get_chrome_sessions', return_value=([session], '')), patch.object(b, 'validate_session', return_value=checked), patch.object(b, 'save_credentials') as save:
            result = b.sync_account({'type': 'token_file', 'follow_current_session': True, 'auth_file': str(Path(temp) / 'unused')})
        save.assert_not_called()
        self.assertEqual(result['source_label'], session['source_label'])
        self.assertNotIn('credentials', result)

if __name__ == '__main__': unittest.main()
