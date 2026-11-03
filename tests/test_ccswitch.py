from contextlib import closing
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import urllib.error

ROOT=Path(__file__).resolve().parents[1]/'src'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
cc=load('cc_source','ccswitch_source.py')
m=load('cc_relay','fetch_linksapi.py')
q=load('cc_quotas','fetch_quotas.py')
SITE={'success':True,'data':{'quota_per_unit':500000,'quota_display_type':'USD'}}
USAGE={'code':True,'data':{'object':'token_usage','total_available':750000,'total_used':250000,'total_granted':1000000,'unlimited_quota':False}}

class CCSwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.addCleanup(self.temp.cleanup)
        self.dbpath=self.root/'cc-switch.db'
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            db.execute('CREATE TABLE providers(id TEXT,app_type TEXT,name TEXT,is_current INTEGER,settings_config TEXT,meta TEXT,website_url TEXT,category TEXT)')
            db.execute('CREATE TABLE proxy_request_logs(provider_id TEXT,app_type TEXT,created_at INTEGER,model TEXT,input_tokens INTEGER,output_tokens INTEGER,cache_read_tokens INTEGER,cache_creation_tokens INTEGER,total_cost_usd TEXT,status_code INTEGER)')
        self.provider('a','https://relay.example.com','active-fixture',True)
        self.settings('a')
        self.addCleanup(patch.stopall)
        patch.object(cc,'CC_ROOT',self.root).start();patch.object(m,'_CC',cc).start()
        patch.dict(m.os.environ,{'AI_QUOTA_DATA_DIR':str(self.root/'cache')}).start()
    def provider(self,id,url,key,current=False,meta=None,app='claude'):
        config={'env':{'ANTHROPIC_BASE_URL':url,'ANTHROPIC_AUTH_TOKEN':key}}
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            db.execute('INSERT INTO providers VALUES(?,?,?,?,?,?,?,?)',(id,app,'Fixture '+id,int(current),json.dumps(config),json.dumps(meta or {}),url,'custom'))
    def settings(self,id):
        (self.root/'settings.json').write_text(json.dumps({'currentProviderClaude':id}))
    def cfg(self):return {'enabled':True,'credential_source':'ccswitch','ccswitch_app':'claude'}
    def logs(self):
        return {'success':True,'data':[{'type':2,'created_at':int(time.time()),'model_name':'test-model','quota':100000,'prompt_tokens':100,'completion_tokens':50,'content':'private request text','request_id':'private-id','ip':'private-address'}]}
    def responses(self,path,key=None,user_id=None,**kwargs):
        if path=='/api/status':return SITE
        if path=='/api/log/token':return self.logs()
        return USAGE

    def test_selected_device_provider_overrides_database_flag(self):
        self.provider('b','https://other.example.com','different-fixture',True)
        self.settings('b')
        p=cc.read_current();self.assertEqual(p['id'],'b');self.assertEqual(p['origin'],'https://other.example.com')
    def test_missing_provider_never_falls_back_to_another_account(self):
        self.settings('removed')
        with patch.object(m,'request_json') as request:
            value=m.fetch_quota(self.cfg())
        self.assertFalse(value['ok']);self.assertNotIn('provider_name',value);request.assert_not_called()
    def test_claude_never_uses_another_client_key(self):
        raw={'env':{'ANTHROPIC_BASE_URL':'https://relay.example.com','GEMINI_API_KEY':'other-client-fixture'}}
        with closing(sqlite3.connect(self.dbpath)) as db, db:db.execute('UPDATE providers SET settings_config=?',(json.dumps(raw),))
        with patch.object(m,'request_json') as request:r=m.fetch_quota(self.cfg())
        self.assertEqual(r['state'],'not_relay');request.assert_not_called()

    def test_reads_do_not_modify_ccswitch_database(self):
        before=self.dbpath.read_bytes();p=cc.read_current();self.assertEqual(p['api_key'],'active-fixture')
        self.assertEqual(self.dbpath.read_bytes(),before)
    def test_corrupt_selection_file_does_not_query_stale_database_selection(self):
        (self.root/'settings.json').write_text('broken')
        with patch.object(m,'request_json') as request:
            value=m.fetch_quota(self.cfg())
        self.assertFalse(value['ok']);request.assert_not_called()
    def test_dynamic_origin_receives_only_its_own_key(self):
        self.provider('b','https://second.example.com/v1','second-fixture')
        self.settings('b')
        with patch.object(m,'request_json',side_effect=self.responses) as request:
            value=m.fetch_quota(self.cfg(),force=True)
        self.assertTrue(value['ok']);self.assertEqual(value['provider_name'],'Fixture b')
        self.assertEqual(value['remaining_amount'],1.5)
        self.assertTrue(all(c.kwargs['origin']=='https://second.example.com' for c in request.call_args_list))
        self.assertEqual(request.call_args_list[1].args[1],'second-fixture')
        self.assertNotIn('second-fixture',json.dumps(value));self.assertNotIn('active-fixture',json.dumps(value))
    def test_source_switch_and_key_rotation_bypass_old_backoff(self):
        with patch.object(m,'request_json',side_effect=self.responses):old=m.fetch_quota(self.cfg())
        old['next_retry_at']=time.time()+200
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            db.execute('UPDATE providers SET settings_config=? WHERE id=?',(json.dumps({'env':{'ANTHROPIC_BASE_URL':'https://relay.example.com','ANTHROPIC_AUTH_TOKEN':'rotated-fixture'}}),'a'))
        with patch.object(m,'request_json',side_effect=self.responses) as request:
            new=m.fetch_quota(self.cfg(),previous=old)
        self.assertNotEqual(old['source_signature'],new['source_signature']);self.assertTrue(new['ok']);self.assertGreater(request.call_count,0)
    def test_same_provider_backoff_performs_no_network_calls(self):
        with patch.object(m,'request_json',side_effect=self.responses):old=m.fetch_quota(self.cfg())
        old['next_retry_at']=time.time()+200
        with patch.object(m,'request_json') as request:
            new=m.fetch_quota(self.cfg(),previous=old)
        self.assertIs(old,new);request.assert_not_called()
    def test_unsafe_and_cross_origin_urls_are_rejected(self):
        for url in ['http://relay.example.com','https://127.0.0.1','https://192.168.1.1','https://user:pass@relay.example.com','https://relay.example.com?key=fixture','https://relay.example.com:444']:
            with self.assertRaises(cc.SourceError):cc.origin_url(url)
        self.provider('b','https://second.example.com','fixture',meta={'usage_script':{'baseUrl':'https://different.example.com'}})
        self.settings('b')
        with patch.object(m,'request_json') as request:r=m.fetch_quota(self.cfg())
        self.assertEqual(r['state'],'origin_mismatch');request.assert_not_called()
    def test_official_provider_without_relay_key_does_not_query(self):
        with closing(sqlite3.connect(self.dbpath)) as db, db:db.execute('UPDATE providers SET settings_config=?',('{"auth":{},"config":""}',))
        with patch.object(m,'request_json') as request:r=m.fetch_quota(self.cfg())
        self.assertEqual(r['state'],'not_relay');request.assert_not_called()
    def test_toml_selected_codex_provider_is_parsed(self):
        if cc.tomllib is None:self.skipTest('Use bundled Python 3.13 for TOML')
        raw={'env':{'ANTHROPIC_BASE_URL':'https://wrong.example.com','ANTHROPIC_AUTH_TOKEN':'wrong-fixture'},'auth':{'OPENAI_API_KEY':'codex-fixture'},'config':'model_provider = "relay"\n[model_providers.relay]\nbase_url = "https://codex.example.com/v1"\n'}
        with closing(sqlite3.connect(self.dbpath)) as db, db:db.execute('UPDATE providers SET app_type=?,settings_config=?',('codex',json.dumps(raw)))
        (self.root/'settings.json').write_text('{"currentProviderCodex":"a"}')
        p=cc.read_current('codex');self.assertEqual(p['origin'],'https://codex.example.com');self.assertEqual(p['api_key'],'codex-fixture')
    def test_optional_ccswitch_account_credentials_query_wallet(self):
        with closing(sqlite3.connect(self.dbpath)) as db, db:db.execute('UPDATE providers SET meta=?',(json.dumps({'usage_script':{'accessToken':'access-fixture','userId':'123'}}),))
        def response(path,*args,**kwargs):
            return {'success':True,'data':{'quota':5000000,'used_quota':1000000}} if path=='/api/user/self' else self.responses(path,*args,**kwargs)
        with patch.object(m,'request_json',side_effect=response) as request:r=m.fetch_quota(self.cfg())
        self.assertEqual(r['remaining_amount'],10);self.assertEqual(request.call_args_list[1].args,('/api/user/self','access-fixture','123'))
        self.assertNotIn('access-fixture',json.dumps(r))
    def test_site_logs_are_aggregated_without_retaining_raw_records(self):
        with patch.object(m,'request_json',side_effect=self.responses):r=m.fetch_quota(self.cfg(),force=True)
        a=r['analysis'];self.assertTrue(a['available']);self.assertEqual(a['today']['requests'],1)
        self.assertEqual(a['today']['tokens'],150);self.assertEqual(a['today']['amount'],.2)
        cached=(self.root/'cache/ccswitch_analysis.json').read_text()
        for private in ['active-fixture','private request text','private-id','private-address']:self.assertNotIn(private,cached)
        self.assertEqual((self.root/'cache/ccswitch_analysis.json').stat().st_mode&0o777,0o600)
    def test_analysis_cache_prevents_repeated_log_requests(self):
        with patch.object(m,'request_json',side_effect=self.responses) as request:
            m.fetch_quota(self.cfg());m.fetch_quota(self.cfg())
            self.assertEqual(sum(c.args[0]=='/api/log/token' for c in request.call_args_list),1)
            m.fetch_quota(self.cfg(),force=True)
            self.assertEqual(sum(c.args[0]=='/api/log/token' for c in request.call_args_list),2)
    def test_corrupt_analysis_cache_does_not_destroy_valid_site_usage(self):
        directory=self.root/'cache';directory.mkdir()
        with patch.object(m,'request_json',side_effect=self.responses):r=m.fetch_quota(self.cfg())
        good=json.loads((directory/'ccswitch_analysis.json').read_text())
        malformed=json.loads(json.dumps(good));malformed['analysis']['today']['requests']='corrupt'
        huge_time=json.loads(json.dumps(good));huge_time['checked_at']=10**500
        for broken in [[], None, {'analysis':'broken'}, malformed, huge_time]:
            with self.subTest(cache=type(broken).__name__):
                (directory/'ccswitch_analysis.json').write_text(json.dumps(broken))
                with patch.object(m,'request_json',side_effect=self.responses):r=m.fetch_quota(self.cfg())
                self.assertTrue(r['ok']);self.assertTrue(r['analysis']['available'])
                self.assertEqual(r['analysis']['today']['tokens'],150)

    def test_unavailable_logs_do_not_destroy_valid_quota(self):
        def response(path,*args,**kwargs):
            if path=='/api/log/token':raise urllib.error.HTTPError('https://relay.example.com',404,'private-fixture',{},None)
            return self.responses(path,*args,**kwargs)
        with patch.object(m,'request_json',side_effect=response):r=m.fetch_quota(self.cfg())
        self.assertTrue(r['ok']);self.assertFalse(r['analysis']['available']);self.assertNotIn('private-fixture',json.dumps(r))
    def test_local_fallback_is_filtered_by_provider_and_client(self):
        stamp=int(time.time())
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            for provider,client in [('a','claude'),('b','claude'),('a','codex')]:
                db.execute('INSERT INTO proxy_request_logs VALUES(?,?,?,?,?,?,?,?,?,?)',(provider,client,stamp,'local-model',10,20,3,2,'0.1',200))
        value=cc.local_analysis(cc.read_current())
        self.assertTrue(value['available']);self.assertEqual(value['today']['requests'],1);self.assertEqual(value['today']['tokens'],30)
        self.assertIn('估算',value['source'])
    def test_day_boundary_and_invalid_values_do_not_invent_amounts(self):
        now=datetime(2026,11,3,0,30,tzinfo=timezone(timedelta(hours=8)))
        records=[{'time':now.timestamp()-60,'tokens':20,'cost':None,'model':'current'}, {'time':now.timestamp()-3600,'tokens':30,'cost':2,'model':'yesterday'}, {'time':float('nan'),'tokens':10,'cost':99}]
        r=cc.summarize(records,source='fixture',unit='USD',now=now)
        self.assertEqual(r['today']['requests'],1);self.assertEqual(r['week']['requests'],2)
        self.assertIsNone(r['today']['amount']);json.dumps(r,allow_nan=False)
    def test_large_token_total_is_unknown_instead_of_overflowing_swift(self):
        rows=[{'time':time.time(),'tokens':9e17,'cost':0} for _ in range(20)]
        r=cc.summarize(rows,source='fixture',unit='USD');self.assertIsNone(r['today']['tokens'])
    def test_overflowing_site_conversion_never_returns_nonfinite_json(self):
        tiny={'success':True,'data':{'quota_per_unit':1e-308,'quota_display_type':'USD'}}
        r=m.parse_usage(USAGE,tiny)
        self.assertFalse(r['ok']);json.dumps(r,allow_nan=False)
        def response(path,*args,**kwargs):
            return tiny if path=='/api/status' else self.responses(path,*args,**kwargs)
        with patch.object(m,'request_json',side_effect=response):r=m.fetch_quota(self.cfg(),force=True)
        self.assertIsNone(r['analysis']['today']['amount']);json.dumps(r,allow_nan=False)
    def test_aggregate_overflow_is_unknown_instead_of_infinite(self):
        r=cc.summarize([{'time':time.time(),'tokens':1,'cost':9e17}]*3,source='fixture',unit='USD')
        self.assertIsNone(r['today']['amount']);json.dumps(r,allow_nan=False)

    def test_read_only_routes_have_no_key_in_url_or_body(self):
        opener=MagicMock();opener.open.return_value=io.BytesIO(json.dumps(self.logs()).encode())
        with patch.object(m.urllib.request,'build_opener',return_value=opener):
            m.request_json('/api/log/token','fixture',origin='https://relay.example.com')
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://relay.example.com/api/log/token');self.assertIsNone(request.data);self.assertEqual(request.get_method(),'GET')
        with self.assertRaises(ValueError):m.request_json('/v1/messages','fixture',origin='https://relay.example.com')
    def test_sub2api_usage_preserves_declared_units_and_site_scope(self):
        def response(path,*args,**kwargs):
            if path=='/api/status':raise urllib.error.HTTPError('https://relay.example.com',404,'fixture',{},None)
            if path=='/v1/usage':return {'isValid':True,'remaining':7,'quota':{'used':3,'limit':10},'unit':'CNY'}
            raise AssertionError('Unexpected endpoint')
        with patch.object(m,'request_json',side_effect=response):r=m.fetch_quota(self.cfg())
        self.assertTrue(r['ok']);self.assertEqual(r['remaining_amount'],7);self.assertEqual(r['used_amount'],3)
        self.assertEqual(r['amount_unit'],'CNY');self.assertEqual(r['dashboard_url'],'https://relay.example.com')

    def test_unknown_service_protocol_does_not_copy_previous_provider_amounts(self):
        with patch.object(m,'request_json',return_value={'message':'unrecognized'}):r=m.fetch_quota(self.cfg(),previous={'ok':True,'remaining_amount':123})
        self.assertFalse(r['ok']);self.assertNotIn('remaining_amount',r)
    def test_parent_spaces_successful_ccswitch_network_checks(self):
        result={'ok':True,'source_signature':'same'}
        with patch.object(q,'ROOT',self.root),patch.object(q,'read_json',return_value={}),patch.object(q,'query_linksapi_quota',return_value=result),patch.object(q,'atomic_json') as save:
            q.fetch_linksapi_quota({'linksapi':self.cfg()})
        delay=save.call_args.args[1]['next_retry_at']-time.time()
        self.assertTrue(295 < delay <= 300)
    def test_parent_waits_five_minutes_after_site_rate_limit(self):
        result={'ok':False,'source_signature':'same','http_status':429}
        with patch.object(q,'ROOT',self.root),patch.object(q,'read_json',return_value={}),patch.object(q,'query_linksapi_quota',return_value=result),patch.object(q,'atomic_json') as save:
            q.fetch_linksapi_quota({'linksapi':self.cfg()})
        delay=save.call_args.args[1]['next_retry_at']-time.time()
        self.assertTrue(295 < delay <= 300)

    def test_parent_backoff_resets_when_ccswitch_source_changes(self):
        path=self.root/'quota.json';old={'source_signature':'old','failure_count':5,'next_retry_at':time.time()+200}
        new={'ok':False,'source_signature':'new','status_msg':'fixture'}
        with patch.object(q,'ROOT',self.root),patch.object(q,'read_json',return_value=old),patch.object(q,'query_linksapi_quota',return_value=new),patch.object(q,'atomic_json') as save:
            q.fetch_linksapi_quota({'linksapi':self.cfg()})
        self.assertEqual(save.call_args.args[1]['failure_count'],1)

if __name__=='__main__':unittest.main()
