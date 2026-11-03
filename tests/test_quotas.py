import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
s = importlib.util.spec_from_file_location('quotas', Path(__file__).resolve().parents[1] / 'src' / 'fetch_quotas.py')
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)

class QuotaTests(unittest.TestCase):
    def test_missing_window_is_unknown(self):
        self.assertEqual(m.quota_window({}, 'used_percent', 'reset_at'), (None,None,None))
    def test_zero_is_real_zero(self):
        self.assertEqual(m.quota_window({'used_percent':100}, 'used_percent', 'reset_at')[:2], (0,100))
        self.assertEqual(m.quota_window({'remainingFraction':0}, 'remainingFraction', 'resetTime', True)[:2], (0,100))
    def test_invalid_and_out_of_range_values(self):
        self.assertIsNone(m.percent(float('nan')))
        self.assertIsNone(m.percent(True))
        self.assertIsNone(m.percent('75'))
        self.assertEqual(m.percent(120),100)
        self.assertEqual(m.percent(-20),0)
    def test_weekly_only_service(self):
        d=m.parse_agy_summary({'groups':[{'displayName':'Gemini Models','buckets':[{'window':'weekly','remainingFraction':.03,'resetTime':'2026-10-26T12:39:10Z'}]}]}, {})
        self.assertIsNone(d['gemini_5h_rem'])
        self.assertEqual(d['gemini_weekly_rem'],3)
        self.assertIsNone(d['claude_weekly_rem'])
        self.assertGreater(d['gemini_weekly_reset_at'],0)
    def test_expired_login_never_becomes_connected(self):
        cfg={'id':'test','email':'a@example.com','auth_file':'unused'}
        err=urllib.error.HTTPError('https://chatgpt.com',401,'Unauthorized',{},None)
        with patch.object(m,'read_json',return_value={'access_token':'fake'}), patch.object(m, 'open_usage',side_effect=err):
            d=m.query_account_quota(cfg,{'p_rem':92,'free_resets':2})
        self.assertFalse(d['ok']); self.assertFalse(d['connected'])
        self.assertIsNone(d['p_rem']); self.assertIsNone(d['free_resets'])
        self.assertIn('401',d['status_msg'])
    def test_failed_query_preserves_real_success_timestamp(self):
        old={'last_success_at':'2026-10-18T01:00:00+00:00','p_rem':42}
        with patch.object(m,'read_json',return_value={}):
            d=m.query_account_quota({'id':'test','auth_file':'none'},old)
        self.assertFalse(d['ok']); self.assertEqual(d['last_success_at'],old['last_success_at'])
        self.assertEqual(d['p_rem'],42)
    def test_account_mismatch_stops_request(self):
        with patch.object(m,'read_json',return_value={'access_token':'fake','email':'b@example.com'}), patch.object(m, 'open_usage') as req:
            d=m.query_account_quota({'id':'test','email':'a@example.com','auth_file':'unused'})
        req.assert_not_called(); self.assertFalse(d['ok']); self.assertIn('不一致',d['status_msg'])
    def test_success_does_not_invent_secondary_usage(self):
        data={'plan_type':'team','rate_limit':{'primary_window':{'used_percent':21,'limit_window_seconds':18000,'reset_at':1792419720}}}
        response=io.BytesIO(json.dumps(data).encode())
        with patch.object(m,'read_json',return_value={'access_token':'fake'}),patch.object(m, 'open_usage',return_value=response):
            d=m.query_account_quota({'id':'test','auth_file':'unused'})
        self.assertTrue(d['ok']);self.assertEqual(d['p_rem'],79);self.assertIsNone(d['s_rem']); self.assertEqual(d['p_window_mins'],300)
    def test_disabled_accounts_removed_and_failures_kept_in_order(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(m,'ROOT',Path(temp)),patch.object(m,'GPT_ACCOUNTS_CACHE',Path(temp)/'accounts.json'):
            cfg={'chatgpt_accounts':[{'id':'first','auth_file':'missing'},{'id':'disabled','enabled':False},{'id':'last','auth_file':'missing'}]}
            m.fetch_all_gpt_accounts(cfg)
            d=json.loads(m.GPT_ACCOUNTS_CACHE.read_text())
        self.assertEqual([a['id'] for a in d],['first','last']);self.assertTrue(all(not a['ok'] for a in d))
    def test_ag_failure_marks_old_cache_disconnected(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(m,'ROOT',Path(temp)),patch.object(m,'AGY_CACHE',Path(temp)/'agy.json'),patch.object(m,'retrieve_agy_summary',return_value=None):
            m.atomic_json(m.AGY_CACHE,{'ok':True,'last_success_at':'2026-10-18T01:00:00+00:00','gemini_weekly_rem':3})
            m.fetch_antigravity_quota({})
            d=json.loads(m.AGY_CACHE.read_text())
        self.assertFalse(d['ok']); self.assertEqual(d['last_success_at'],'2026-10-18T01:00:00+00:00')

if __name__=='__main__': unittest.main()
