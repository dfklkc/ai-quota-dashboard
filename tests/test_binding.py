import base64
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
s=importlib.util.spec_from_file_location('binding',Path(__file__).resolve().parents[1] / 'src'/'bind_account.py')
b=importlib.util.module_from_spec(s);s.loader.exec_module(b)
qspec=importlib.util.spec_from_file_location('quotas',Path(__file__).resolve().parents[1] / 'src'/'fetch_quotas.py')
q=importlib.util.module_from_spec(qspec);qspec.loader.exec_module(q)
USAGE={'plan_type':'team','rate_limit':{'primary_window':{'used_percent':8,'limit_window_seconds':18000},'secondary_window':{'used_percent':6,'limit_window_seconds':604800}}}

class BindingTests(unittest.TestCase):
    def test_browser_js_permission_is_not_reported_as_no_tabs(self):
        p=subprocess.CompletedProcess([],0,'{"code":"javascript_disabled"}\n','')
        with patch.object(b.subprocess,'run',return_value=p) as run:
            sessions,error=b.get_chrome_sessions()
        self.assertEqual(error,'javascript_disabled');self.assertEqual(sessions,[])
        self.assertNotIn('shell',run.call_args.kwargs)
    def test_automation_denied(self):
        with patch.object(b.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','err -1743')):
            self.assertEqual(b.get_chrome_sessions()[1],'automation_denied')
    def test_browser_timeout(self):
        with patch.object(b.subprocess,'run',side_effect=subprocess.TimeoutExpired('stub',11)):
            self.assertEqual(b.get_chrome_sessions()[1],'timeout')
    def test_no_tab_and_not_logged_in_are_distinct(self):
        for code in ['no_tab','not_logged_in','chrome_closed']:
            with patch.object(b.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps({'code':code}),'')):
                self.assertEqual(b.get_chrome_sessions()[1],code)
    def test_mismatch_does_not_request_or_write(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(b,'ROOT',Path(tmp)),patch.object(b,'get_chrome_sessions',return_value=([{'email':'other@example.com','access_token':'secret'}],'invalid_session')),patch.object(b, 'open_usage') as req:
            out=Path(tmp)/'token.json';out.write_text('old')
            r=b.sync_account({'id':'test','type':'token_file','email':'wanted@example.com','auth_file':str(out)})
            self.assertEqual(r['code'],'account_mismatch');self.assertEqual(out.read_text(),'old');req.assert_not_called()
    def test_native_login_is_never_overwritten(self):
        with patch.object(b,'get_chrome_sessions') as browser:
            r=b.sync_account({'type':'token_file','email':'a@example.com','auth_file':'~/.codex/auth.json'})
        self.assertEqual(r['code'],'unsupported');browser.assert_not_called()
    def test_success_is_private_atomic_and_preserves_usage(self):
        session={'email':'a@example.com','access_token':'test-secret','account_id':'account-test'}
        with tempfile.TemporaryDirectory() as tmp,patch.object(b,'ROOT',Path(tmp)),patch.object(b,'get_chrome_sessions',return_value=([session],'invalid_session')),patch.object(b, 'open_usage',return_value=io.BytesIO(json.dumps(USAGE).encode())):
            out=Path(tmp)/'token.json';r=b.sync_account({'type':'token_file','email':'a@example.com','auth_file':str(out)})
            self.assertTrue(r['ok']);self.assertEqual(r['usage'],USAGE);self.assertNotIn('credentials',r)
            self.assertEqual(out.stat().st_mode&0o777,0o600);self.assertEqual(json.loads(out.read_text())['access_token'],'test-secret')
            self.assertFalse(list(Path(tmp).glob('.session-*')))
    def test_cloud_failure_does_not_overwrite_token(self):
        e=urllib.error.HTTPError('https://chatgpt.com',403,'Forbidden',{},None)
        session={'email':'a@example.com','access_token':'secret'}
        with tempfile.TemporaryDirectory() as tmp,patch.object(b,'ROOT',Path(tmp)),patch.object(b,'get_chrome_sessions',return_value=([session],'')),patch.object(b, 'open_usage',side_effect=e):
            out=Path(tmp)/'token.json';out.write_text('old')
            r=b.sync_account({'type':'token_file','email':'a@example.com','auth_file':str(out)})
            self.assertFalse(r['ok']);self.assertIn('403',r['message']);self.assertEqual(out.read_text(),'old')
    def test_claim_identity_mismatch_is_rejected(self):
        claims=base64.urlsafe_b64encode(json.dumps({'email':'b@example.com'}).encode()).decode().rstrip('=')
        with patch.object(b, 'open_usage') as req:
            r=b.validate_session({'email':'a@example.com','access_token':'header.'+claims+'.signature'},'a@example.com')
        self.assertFalse(r['ok']);req.assert_not_called()
    def test_401_auto_sync_uses_validated_usage(self):
        cfg={'id':'test','type':'token_file','email':'a@example.com','auth_file':'unused','auto_sync_chrome':True}
        e=urllib.error.HTTPError('https://chatgpt.com',401,'Unauthorized',{},None)
        with patch.object(q,'read_json',return_value={'access_token':'old'}),patch.object(q, 'open_usage',side_effect=e),patch.object(q,'sync_chrome_account',return_value=b.result('success',usage=USAGE)) as sync:
            r=q.query_account_quota(cfg)
        self.assertTrue(r['ok']);self.assertEqual(r['p_rem'],92);self.assertEqual(r['s_rem'],94);sync.assert_called_once()
    def test_browser_permission_error_visible_in_card(self):
        cfg={'id':'test','type':'token_file','auth_file':'missing','auto_sync_chrome':True}
        with patch.object(q,'read_json',return_value={}),patch.object(q,'sync_chrome_account',return_value=b.result('javascript_disabled')):
            r=q.query_account_quota(cfg)
        self.assertFalse(r['ok']);self.assertIn('Apple',r['status_msg'])
    def test_auto_sync_off_never_reads_browser(self):
        cfg={'id':'test','type':'token_file','auth_file':'missing','auto_sync_chrome':False}
        with patch.object(q,'read_json',return_value={}),patch.object(q,'sync_chrome_account') as sync:
            r=q.query_account_quota(cfg)
        self.assertFalse(r['ok']);sync.assert_not_called()

if __name__=='__main__':unittest.main()
