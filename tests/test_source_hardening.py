"""Offline malformed-data and credential-routing regressions."""
import importlib.util
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1] / 'src'

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

q = load('hardened_quotas', 'fetch_quotas.py')
b = load('hardened_binding', 'bind_account.py')
USAGE = {'rate_limit': {'primary_window': {'used_percent': 20}}}

class SourceHardeningTests(unittest.TestCase):
    def test_boolean_fraction_is_unknown_instead_of_a_full_bucket(self):
        for value in [True, False, float('nan'), float('inf')]:
            with self.subTest(value=value):
                self.assertEqual(q.quota_window({'remainingFraction': value}, 'remainingFraction', 'resetTime', True)[:2], (None, None))
        self.assertEqual(q.quota_window({'remainingFraction': 0}, 'remainingFraction', 'resetTime', True)[:2], (0, 100))

    def test_invalid_reset_times_do_not_poison_cache_writes(self):
        for value in [True, False, float('nan'), float('inf'), -1, 0, 10 ** 400, 1e300]:
            with self.subTest(value=type(value).__name__):
                _, _, reset = q.quota_window({'used_percent': 20, 'reset_at': value}, 'used_percent', 'reset_at')
                self.assertIsNone(reset)
                with tempfile.TemporaryDirectory() as directory, patch.object(q, 'ROOT', Path(directory)):
                    path = Path(directory) / 'cache.json'
                    q.atomic_json(path, {'reset_at': reset})
                    self.assertIsNone(json.loads(path.read_text())['reset_at'])

    def test_optional_bad_duration_does_not_drop_valid_usage(self):
        for value in [None, '18000', True, float('nan'), float('inf'), -30, 0, 10 ** 400, 1e300]:
            with self.subTest(value=type(value).__name__):
                data = {'rate_limit': {'primary_window': {'used_percent': 20, 'limit_window_seconds': value}}}
                out = q.apply_usage({'ok': False}, data)
                self.assertTrue(out['ok'])
                self.assertEqual(out['p_rem'], 80)
                self.assertIsNone(out['p_window_mins'])
                self.assertIsNone(out['s_rem'])
        self.assertEqual(q.window_minutes(18000), 300)

    def test_wrong_window_shape_is_unknown_without_losing_other_window(self):
        out = q.apply_usage({'ok': False}, {'rate_limit': {'primary_window': [], 'secondary_window': {'used_percent': 12}}})
        self.assertTrue(out['ok'])
        self.assertIsNone(out['p_rem'])
        self.assertEqual(out['s_rem'], 88)

    def test_both_gpt_readers_refuse_redirects_before_credentials_reach_target(self):
        calls = []
        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append((self.path, self.headers.get('Authorization')))
                self.send_response(302 if self.path == '/start' else 200)
                if self.path == '/start':
                    self.send_header('Location', 'http://localhost:%s/unexpected' % self.server.server_port)
                self.send_header('Content-Length', '0')
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), RedirectHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            for module in (q, b):
                with self.subTest(reader=module.__name__):
                    calls.clear()
                    request = urllib.request.Request('http://127.0.0.1:%s/start' % server.server_port,
                                                     headers={'Authorization': 'Bearer test-only'})
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        module.open_usage(request, timeout=1)
                    self.assertEqual(error.exception.code, 302)
                    self.assertEqual(calls, [('/start', 'Bearer test-only')])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_follow_bridge_command_requests_first_session_only(self):
        done = subprocess.CompletedProcess([], 0, '{"email":"active@example.com","access_token":"stub"}\n', '')
        with patch.object(b.subprocess, 'run', return_value=done) as run:
            sessions, error = b.get_chrome_sessions(first_session=True)
        self.assertEqual(error, '')
        self.assertEqual(sessions[0]['email'], 'active@example.com')
        self.assertIn('--first-session', run.call_args.args[0])
        with patch.object(b.subprocess, 'run', return_value=done) as run:
            b.get_chrome_sessions()
        self.assertNotIn('--first-session', run.call_args.args[0])

    def test_fixed_email_binding_still_searches_all_sessions(self):
        sessions = [{'email': 'other@example.com', 'access_token': 'other-stub'},
                    {'email': 'wanted@example.com', 'access_token': 'wanted-stub'}]
        with tempfile.TemporaryDirectory() as directory, patch.object(b, 'ROOT', Path(directory)), \
             patch.object(b, 'get_chrome_sessions', return_value=(sessions, '')) as browser, \
             patch.object(b, 'open_usage', return_value=io.BytesIO(json.dumps(USAGE).encode())):
            credential = Path(directory) / 'token.json'
            result = b.sync_account({'type': 'token_file', 'email': 'wanted@example.com', 'auth_file': str(credential)})
            self.assertTrue(result['ok'])
            self.assertEqual(json.loads(credential.read_text())['email'], 'wanted@example.com')
        browser.assert_called_once_with(first_session=False)

    def test_follow_binding_selects_priority_session_without_saving_token(self):
        session = {'email': 'current@example.com', 'access_token': 'stub'}
        with tempfile.TemporaryDirectory() as directory, patch.object(b, 'ROOT', Path(directory)), \
             patch.object(b, 'get_chrome_sessions', return_value=([session], '')) as browser, \
             patch.object(b, 'open_usage', return_value=io.BytesIO(json.dumps(USAGE).encode())), \
             patch.object(b, 'save_credentials') as save:
            result = b.sync_account({'type': 'token_file', 'follow_current_session': True})
        self.assertTrue(result['ok'])
        browser.assert_called_once_with(first_session=True)
        save.assert_not_called()

    def test_boolean_or_nonfinite_usage_cannot_validate_a_browser_credential(self):
        for value in [True, float('nan'), float('inf')]:
            with self.subTest(value=value), patch.object(b, 'open_usage', return_value=io.BytesIO(json.dumps({'rate_limit': {'primary_window': {'used_percent': value}}}).encode())):
                result = b.validate_session({'email': 'current@example.com', 'access_token': 'stub'}, 'current@example.com')
                self.assertFalse(result['ok'])
                self.assertNotIn('credentials', result)

    def test_optional_plan_and_reset_count_always_match_swift_field_types(self):
        for plan in [None, 12, True, [], {}, float('nan')]:
            for count in [None, '2', True, -1, 2.5, 2 ** 63, float('nan'), float('inf')]:
                with self.subTest(plan=type(plan).__name__, count=type(count).__name__):
                    payload = dict(USAGE, plan_type=plan, rate_limit_reset_credits={'available_count': count})
                    out = q.apply_usage({'ok': False}, payload)
                    self.assertTrue(out['ok'])
                    self.assertIsInstance(out['plan'], str)
                    self.assertIsNone(out['free_resets'])
                    json.dumps(out, allow_nan=False)
        out = q.apply_usage({'ok': False}, dict(USAGE, plan_type=' team ', rate_limit_reset_credits={'available_count': 2.0}))
        self.assertEqual(out['plan'], 'team')
        self.assertEqual(out['free_resets'], 2)
        self.assertIsInstance(out['free_resets'], int)
        self.assertIsNone(q.apply_usage({'ok': False}, dict(USAGE, rate_limit_reset_credits=[]))['free_resets'])

    def test_corrupt_backoff_metadata_cannot_block_gpt_recovery(self):
        cfg = {'chatgpt_accounts': [{'id': 'app'}]}
        for retry_at in ['invalid', None, True, float('nan'), float('inf'), 10000000]:
            cached = [{'id': 'app', 'failure_count': 'invalid', 'next_retry_at': retry_at}]
            writes = []
            with self.subTest(retry_at=retry_at), patch.object(q.time, 'time', return_value=1000), \
                 patch.object(q, 'read_json', return_value=cached), patch.object(q, 'atomic_json', side_effect=lambda path, data: writes.append(data)), \
                 patch.object(q, 'query_account_quota', return_value={'id': 'app', 'ok': False}) as request:
                q.fetch_all_gpt_accounts(cfg)
            request.assert_called_once()
            self.assertEqual(writes[-1][0]['failure_count'], 1)
            self.assertEqual(writes[-1][0]['next_retry_at'], 1060)

    def test_normal_backoff_is_respected_and_success_clears_it(self):
        with patch.object(q.time, 'time', return_value=1000):
            self.assertTrue(q.retry_pending({'next_retry_at': 1060}))
            self.assertFalse(q.retry_pending({'next_retry_at': 999}))
            restored = q.record_attempt({'ok': True}, {'failure_count': 'invalid'})
        self.assertEqual(restored['failure_count'], 0)
        self.assertEqual(restored['next_retry_at'], 0)

    def test_corrupt_relay_and_antigravity_backoff_recover(self):
        invalid = {'failure_count': None, 'next_retry_at': 'tomorrow'}
        with patch.object(q, 'read_json', return_value=invalid), patch.object(q, 'atomic_json') as save, \
             patch.object(q, 'retrieve_agy_summary', return_value={'groups': []}) as query:
            q.fetch_antigravity_quota({'antigravity': {'enabled': True}})
        query.assert_called_once()
        self.assertEqual(save.call_args.args[1]['failure_count'], 1)
        with patch.object(q, 'read_json', return_value=invalid), patch.object(q, 'atomic_json') as save, \
             patch.object(q, 'query_linksapi_quota', return_value={'ok': False}) as query:
            q.fetch_linksapi_quota({'linksapi': {'enabled': True}})
        query.assert_called_once()
        self.assertEqual(save.call_args.args[1]['failure_count'], 1)

    def test_disabled_sources_do_not_read_credentials_import_or_rewrite_cache(self):
        with patch.object(q, 'read_json') as read, patch.object(q, 'atomic_json') as save, \
             patch.object(q, 'retrieve_agy_summary') as agy, patch.object(q, 'query_linksapi_quota') as relay, \
             patch.object(q, 'query_account_quota') as gpt:
            q.fetch_all_gpt_accounts({'chatgpt_accounts': [{'id': 'app', 'enabled': False}]})
            q.fetch_antigravity_quota({'antigravity': {'enabled': False}}, force=True)
            q.fetch_linksapi_quota({'linksapi': {'enabled': False}}, force=True)
        for operation in [read, save, agy, relay, gpt]:
            operation.assert_not_called()

    def test_dispatch_only_schedules_enabled_sources_even_for_manual_refresh(self):
        config = {'chatgpt_accounts': [{'id': 'app', 'enabled': True}],
                  'antigravity': {'enabled': False}, 'linksapi': {'enabled': False}}
        with tempfile.TemporaryDirectory() as directory, patch.object(q, 'ROOT', Path(directory)), \
             patch.object(q, 'read_json', return_value=config), patch.object(sys, 'argv', ['fetch_quotas.py', '--force']), \
             patch.object(q, 'fetch_all_gpt_accounts') as gpt, patch.object(q, 'fetch_antigravity_quota') as agy, \
             patch.object(q, 'fetch_linksapi_quota') as relay:
            q.main()
        gpt.assert_called_once_with(config, True, None)
        agy.assert_not_called()
        relay.assert_not_called()

    def test_backoff_without_changes_does_not_rewrite_gpt_cache(self):
        cached = [{'id': 'app', 'ok': False, 'next_retry_at': 1060}]
        with patch.object(q.time, 'time', return_value=1000), patch.object(q, 'read_json', return_value=cached), \
             patch.object(q, 'query_account_quota') as query, patch.object(q, 'atomic_json') as save:
            q.fetch_all_gpt_accounts({'chatgpt_accounts': [{'id': 'app'}]})
        query.assert_not_called()
        save.assert_not_called()

    def test_backoff_still_removes_an_account_disabled_in_config(self):
        cached = [{'id': 'app', 'ok': False, 'next_retry_at': 1060}, {'id': 'disabled', 'ok': True}]
        with patch.object(q.time, 'time', return_value=1000), patch.object(q, 'read_json', return_value=cached), \
             patch.object(q, 'query_account_quota') as query, patch.object(q, 'atomic_json') as save:
            q.fetch_all_gpt_accounts({'chatgpt_accounts': [{'id': 'app'}, {'id': 'disabled', 'enabled': False}]})
        query.assert_not_called()
        save.assert_called_once_with(q.GPT_ACCOUNTS_CACHE, [cached[0]])

    def test_legacy_optional_metadata_is_repaired_without_bypassing_backoff(self):
        cached = [{'id': 'app', 'ok': False, 'next_retry_at': 1060, 'plan': 42, 'free_resets': '2'}]
        with patch.object(q.time, 'time', return_value=1000), patch.object(q, 'read_json', return_value=cached), \
             patch.object(q, 'query_account_quota') as query, patch.object(q, 'atomic_json') as save:
            q.fetch_all_gpt_accounts({'chatgpt_accounts': [{'id': 'app'}]})
        query.assert_not_called()
        repaired = save.call_args.args[1][0]
        self.assertIsNone(repaired['plan'])
        self.assertIsNone(repaired['free_resets'])
        self.assertEqual(repaired['next_retry_at'], 1060)

if __name__ == '__main__':
    unittest.main()
