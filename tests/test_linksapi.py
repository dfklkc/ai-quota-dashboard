import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

spec = importlib.util.spec_from_file_location('linksapi', Path(__file__).resolve().parents[1] / 'src' / 'fetch_linksapi.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
SITE = {'success': True, 'data': {'quota_per_unit': 500000, 'quota_display_type': 'USD'}}
def usage(**overrides):
    data = {'object': 'token_usage', 'name': 'Claude', 'total_granted': 1000000, 'total_used': 250000, 'total_available': 750000, 'unlimited_quota': False}
    data.update(overrides)
    return {'code': True, 'data': data}

class LinksAPITests(unittest.TestCase):
    def test_disabled_source_never_reads_credentials_or_network(self):
        with patch.object(m, 'request_json') as request, patch.object(Path, 'read_text') as read:
            result = m.fetch_quota({'enabled': False})
        self.assertEqual(result['state'], 'not_configured')
        request.assert_not_called(); read.assert_not_called()

    def test_site_scale_yields_monetary_amounts(self):
        r = m.parse_usage(usage(), SITE)
        self.assertTrue(r['ok']); self.assertEqual(r['remaining_amount'], 1.5)
        self.assertEqual(r['used_amount'], .5); self.assertEqual(r['amount_unit'], '站内 USD')

    def test_real_zero_and_negative_balance_are_preserved(self):
        for value in [0, -500000]:
            r = m.parse_usage(usage(total_available=value), SITE)
            self.assertEqual(r['remaining_amount'], value / 500000)
            self.assertTrue(r['ok'])

    def test_unlimited_key_is_not_an_infinite_wallet(self):
        r = m.parse_usage(usage(unlimited_quota=True, total_available=-1), SITE)
        self.assertTrue(r['ok']); self.assertIsNone(r['remaining_amount'])
        self.assertEqual(r['state'], 'usage_only'); self.assertFalse(r['balance_available'])
        self.assertIsNone(r['granted_amount']); self.assertIsNone(r['remaining_pct'])

    def test_unknown_currency_never_uses_an_assumed_conversion(self):
        for site in [None, {'success': False, 'data': SITE['data']}, {'success': True, 'data': {'quota_per_unit': 0, 'quota_display_type': 'USD'}}]:
            r = m.parse_usage(usage(), site)
            self.assertEqual(r['remaining_amount'], 750000)
            self.assertEqual(r['amount_unit'], '额度单位')

    def test_malformed_and_missing_balances_do_not_become_zero(self):
        for value in [None, True, '5', float('nan')]:
            r = m.parse_usage(usage(total_available=value), SITE)
            self.assertFalse(r['ok']); self.assertNotIn('remaining_amount', r)
        self.assertFalse(m.parse_usage({'success': False, 'message': 'secret'}, SITE)['ok'])

    def test_expired_token_is_not_a_current_balance(self):
        with patch.object(m.time, 'time', return_value=100):
            r = m.parse_usage(usage(expires_at=99), SITE)
        self.assertFalse(r['ok']); self.assertEqual(r['state'], 'expired')

    def test_account_mode_reads_wallet_instead_of_key_limit(self):
        r = m.parse_account({'success': True, 'data': {'username': 'test', 'quota': 5000000, 'used_quota': 1000000}}, SITE)
        self.assertEqual(r['remaining_amount'], 10)
        self.assertEqual(r['state'], 'ready'); self.assertTrue(r['balance_available'])
        self.assertEqual(r['used_amount'], 2)
        self.assertIn('账户钱包', r['scope']); self.assertIsNone(r['remaining_pct'])

    def test_account_missing_quota_is_not_zero(self):
        self.assertFalse(m.parse_account({'success': True, 'data': {}}, SITE)['ok'])

    def test_browser_not_needed_for_direct_account_request(self):
        account = {'success': True, 'data': {'quota': 0, 'used_quota': 1000000}}
        with tempfile.TemporaryDirectory() as temp:
            keyfile = Path(temp) / 'key.json'; keyfile.write_text(json.dumps({'access_token': 'stub-secret', 'user_id': '123'}))
            with patch.object(m, 'TOKEN_FILE', keyfile), patch.object(m, 'request_json', side_effect=[account, SITE]) as request:
                r = m.fetch_quota({'enabled': True, 'query_mode': 'account'})
        self.assertEqual(r['remaining_amount'], 0)
        self.assertEqual(request.call_args_list[0].args, ('/api/user/self', 'stub-secret', '123'))
        self.assertNotIn('stub-secret', json.dumps(r))

    def test_unauthorized_response_does_not_echo_secret(self):
        err = urllib.error.HTTPError(m.ORIGIN, 401, 'stub-secret', {}, None)
        with tempfile.TemporaryDirectory() as temp:
            keyfile = Path(temp) / 'key.json'; keyfile.write_text('{"api_key":"stub-secret"}')
            with patch.object(m, 'TOKEN_FILE', keyfile), patch.object(m, 'request_json', side_effect=err):
                r = m.fetch_quota({'enabled': True, 'query_mode': 'token'})
        self.assertFalse(r['ok']); self.assertNotIn('stub-secret', json.dumps(r))

    def test_redirect_refused_and_only_read_endpoints_allowed(self):
        handler = m.NoRedirect()
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(m.urllib.request.Request(m.ORIGIN), None, 302, 'Found', {}, 'https://other.example/')
        with self.assertRaises(ValueError):
            m.request_json('/v1/messages', 'stub-secret')

    def test_model_restrictions_do_not_promise_claude_access(self):
        r = m.parse_usage(usage(model_limits_enabled=True, model_limits={'gpt-4o': True}), SITE)
        self.assertFalse(r['claude_allowed'])

    def test_token_endpoint_uses_canonical_path_without_redirect(self):
        from unittest.mock import MagicMock
        opener = MagicMock()
        opener.open.return_value = io.BytesIO(json.dumps(usage()).encode())
        with patch.object(m.urllib.request, 'build_opener', return_value=opener):
            m.request_json('/api/usage/token', 'stub-secret')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://linksapi.cn/api/usage/token/')
        self.assertEqual(request.get_method(), 'GET')

    def test_follow_claude_reuses_active_config_without_copying_key(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Path(temp) / 'settings.json'
            settings.write_text(json.dumps({'env': {'ANTHROPIC_BASE_URL': m.ORIGIN, 'ANTHROPIC_AUTH_TOKEN': 'active-secret'}}))
            with patch.object(m, 'CLAUDE_SETTINGS', settings), patch.object(m, 'request_json', side_effect=[usage(), SITE]) as request:
                result = m.fetch_quota({'enabled': True, 'credential_source': 'claude_settings'})
            self.assertTrue(result['ok'])
            self.assertEqual(request.call_args_list[0].args, ('/api/usage/token', 'active-secret'))
            self.assertEqual(sorted(p.name for p in Path(temp).iterdir()), ['settings.json'])
            self.assertNotIn('active-secret', json.dumps(result))

    def test_other_provider_key_is_never_sent_to_linksapi(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Path(temp) / 'settings.json'
            settings.write_text(json.dumps({'env': {'ANTHROPIC_BASE_URL': 'https://other.example', 'ANTHROPIC_AUTH_TOKEN': 'other-secret'}}))
            with patch.object(m, 'CLAUDE_SETTINGS', settings), patch.object(m, 'request_json') as request:
                result = m.fetch_quota({'enabled': True, 'credential_source': 'claude_settings'})
            request.assert_not_called()
            self.assertEqual(result['state'], 'provider_changed')

    def test_follow_detects_active_key_rotation(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Path(temp) / 'settings.json'
            with patch.object(m, 'CLAUDE_SETTINGS', settings), patch.object(m, 'request_json', side_effect=[usage(), SITE, usage(), SITE]) as request:
                for key in ['first-secret', 'second-secret']:
                    settings.write_text(json.dumps({'env': {'ANTHROPIC_BASE_URL': m.ORIGIN, 'ANTHROPIC_AUTH_TOKEN': key}}))
                    m.fetch_quota({'enabled': True, 'credential_source': 'claude_settings'})
                self.assertEqual(request.call_args_list[0].args[1], 'first-secret')
                self.assertEqual(request.call_args_list[2].args[1], 'second-secret')

if __name__ == '__main__': unittest.main()
