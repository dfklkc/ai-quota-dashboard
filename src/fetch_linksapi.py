#!/usr/bin/env python3
"""Read-only relay usage from CC Switch or legacy LinksAPI settings. No inference calls."""
import json
import importlib.util
import tempfile
import os
import math
from pathlib import Path
import time
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone

ORIGIN = 'https://linksapi.cn'
DASHBOARD = ORIGIN + '/dashboard/overview'
TOKEN_FILE = Path(os.environ.get('AI_QUOTA_DATA_DIR', str(Path.home() / '.swiftbar'))) / 'tokens/linksapi.json'
CLAUDE_SETTINGS = Path.home() / '.claude/settings.json'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a relay key to another destination.
        raise urllib.error.HTTPError(req.full_url, code, 'Redirect refused', headers, fp)

def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def number(value):
    try:
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    except OverflowError:
        return None

def request_json(path, key=None, user_id=None, *, origin=ORIGIN):
    if path not in ('/api/status', '/api/usage/token', '/api/user/self', '/api/log/token', '/v1/usage'):
        raise ValueError('Unsupported endpoint')
    # The live LinksAPI route canonicalizes this endpoint with a trailing slash.
    if path == '/api/usage/token':
        path += '/'
    if origin != ORIGIN:
        origin = ccswitch_module().origin_url(origin)
    headers = {'Accept': 'application/json', 'User-Agent': 'AIQuotaDashboard/6'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    if user_id is not None:
        if not str(user_id).isdigit():
            raise ValueError('Invalid user ID')
        headers['New-Api-User'] = str(user_id)
    request = urllib.request.Request(origin + path, headers=headers, method='GET')
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=4) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Usage response too large")
        return json.loads(raw)

def empty_result(state, message):
    return dict(ok=False, state=state, status_msg=message, updated_at=timestamp(),
                scope='API 令牌额度', dashboard_url=DASHBOARD)

def parse_usage(payload, site=None):
    if not isinstance(payload, dict) or payload.get('success') is False or payload.get('code') is False:
        return empty_result('api_error', '站点未返回有效用量，请检查令牌')
    data = payload.get('data')
    if not isinstance(data, dict) or data.get('object') != 'token_usage':
        return empty_result('unsupported', '接口格式不匹配，需核对站点版本')
    unlimited = data.get('unlimited_quota') is True
    remaining = number(data.get('total_available'))
    used = number(data.get('total_used'))
    granted = number(data.get('total_granted'))
    if not unlimited and remaining is None:
        return empty_result('missing_usage', '接口未提供令牌剩余额度')
    expires = number(data.get('expires_at'))
    if expires and expires > 0 and expires <= time.time():
        return empty_result('expired', '令牌已到期，请更换 Key')
    # Convert only using the live site's declared scale, never a hardcoded rate.
    site_data = site.get('data', {}) if isinstance(site, dict) and site.get('success') is True and isinstance(site.get('data'), dict) else {}
    divisor = number(site_data.get('quota_per_unit'))
    in_usd = bool(divisor and divisor > 0 and site_data.get('quota_display_type') == 'USD')
    divisor = divisor if in_usd else 1
    remaining = number(remaining / divisor) if remaining is not None else None
    used = number(used / divisor) if used is not None else None
    granted = number(granted / divisor) if granted is not None else None
    if not unlimited and remaining is None:
        return empty_result('missing_usage', '站点额度换算结果超出可显示范围')
    rem_pct = None
    if not unlimited and granted is not None and granted > 0:
        rem_pct = 0 if remaining <= 0 else 100 if remaining >= granted else round(remaining / granted * 100)
    limits = data.get('model_limits')
    restricted = data.get('model_limits_enabled') is True
    claude_allowed = None
    if restricted and isinstance(limits, dict):
        claude_allowed = any('claude' in str(k).lower() and v is True for k, v in limits.items())
    return dict(ok=True, state='usage_only' if unlimited else 'ready',
                status_msg='仅用量已同步，账户余额待配置' if unlimited else '已同步',
                balance_available=not unlimited, token_name=str(data.get('name') or '未命名令牌')[:100],
                scope='API 令牌额度', unlimited=unlimited, remaining_pct=rem_pct,
                remaining_amount=None if unlimited else remaining,
                used_amount=used,
                granted_amount=None if unlimited else granted,
                amount_unit='站内 USD' if in_usd else '额度单位', expires_at=expires if expires and expires > 0 else None,
                claude_allowed=claude_allowed, dashboard_url=DASHBOARD,
                updated_at=timestamp(), last_success_at=timestamp())

def parse_account(payload, site=None):
    if not isinstance(payload, dict) or payload.get('success') is not True or not isinstance(payload.get('data'), dict):
        return empty_result('api_error', '未取得账户钱包余额，请检查访问令牌和用户 ID')
    data = payload['data']
    remaining = number(data.get('quota'))
    used = number(data.get('used_quota'))
    if remaining is None:
        return empty_result('missing_usage', '接口未提供账户余额')
    # Reuse only the site's confirmed monetary conversion.
    parsed = parse_usage({'data': {'object': 'token_usage', 'name': data.get('username') or data.get('display_name') or 'LinksAPI 账户',
                         'total_available': remaining, 'total_used': used,
                         'total_granted': remaining + used if used is not None else None}}, site)
    parsed['scope'] = '账户钱包余额（多模型共享）'
    parsed['remaining_pct'] = None
    return parsed


def fetch_quota(cfg, previous=None, force=False):
    if not cfg.get('enabled', False):
        return empty_result('not_configured', '已预留 · 在设置中配置后启用网络查询')
    if cfg.get('credential_source') == 'ccswitch':
        return fetch_ccswitch(cfg, previous=previous, force=force)
    try:
        mode = cfg.get('query_mode', 'account')
        if cfg.get('credential_source') == 'claude_settings':
            env = json.loads(CLAUDE_SETTINGS.read_text()).get('env', {})
            origin = urllib.parse.urlparse(env.get('ANTHROPIC_BASE_URL', ''))
            if origin.scheme != 'https' or origin.hostname != 'linksapi.cn' or origin.port not in (None, 443):
                return empty_result('provider_changed', 'Claude 当前未使用 LinksAPI，已停止查询')
            saved = {'api_key': env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY') or ''}
            mode = 'token'
        else:
            saved = json.loads(TOKEN_FILE.read_text())
        key = saved.get('access_token' if mode == 'account' else 'api_key', '').strip()
        user_id = saved.get('user_id') if mode == 'account' else None
        if mode == 'account' and (not user_id or not str(user_id).isdigit()):
            return empty_result('not_configured', '账户钱包模式需要访问令牌和用户 ID')
        if not key or any(c.isspace() for c in key):
            return empty_result('not_configured', '请在设置中保存有效的查询凭证')
    except (OSError, ValueError, AttributeError):
        return empty_result('not_configured', '请在设置中填写 LinksAPI 查询凭证')
    try:
        payload = request_json('/api/user/self', key, user_id) if mode == 'account' else request_json('/api/usage/token', key)
        try:
            site = request_json('/api/status')
        except Exception:
            site = None
        return parse_account(payload, site) if mode == 'account' else parse_usage(payload, site)
    except urllib.error.HTTPError as error:
        message = {401: '查询凭证无效或已失效，请更新', 403: '此令牌没有查询权限',
                   429: '查询过于频繁，稍后自动重试'}.get(error.code, '用量接口返回 HTTP %s' % error.code)
        return empty_result('request_failed', message)
    except Exception:
        return empty_result('connection_failed', '站点连接失败或超时，稍后重试')


_CC = None

def ccswitch_module():
    global _CC
    if _CC is None:
        spec = importlib.util.spec_from_file_location('dashboard_ccswitch', Path(__file__).with_name('ccswitch_source.py'))
        _CC = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_CC)
    return _CC


def parse_sub2api(payload):
    if not isinstance(payload, dict) or payload.get('isValid') is False:
        return empty_result('unsupported', '此中转站未提供支持的额度接口')
    quota = payload.get('quota') if isinstance(payload.get('quota'), dict) else {}
    balance = number(payload.get('remaining'))
    used = number(quota.get('used')); limit = number(quota.get('limit'))
    if balance is None and used is None:
        return empty_result('unsupported', '此中转站未返回可确认的余额或用量')
    unit = str(payload.get('unit') or quota.get('unit') or '额度单位')[:20]
    return dict(ok=True, state='ready' if balance is not None else 'usage_only', status_msg='已同步',
                remaining_amount=balance, used_amount=used, granted_amount=limit,
                amount_unit=unit, scope='站点返回的当前 Key 额度',
                updated_at=timestamp(), last_success_at=timestamp())


def valid_analysis(value):
    if not isinstance(value, dict) or not isinstance(value.get('available'), bool): return False
    for field in ('source', 'unit', 'message', 'updated_at'):
        if value.get(field) is not None and not isinstance(value[field], str): return False
    for field in ('sample_count', 'returned_count'):
        if value.get(field) is not None and (type(value[field]) is not int or not 0 <= value[field] <= 5000): return False
    if value['available'] and type(value.get('sample_count')) is not int: return False
    if value.get('sampled') is not None and not isinstance(value['sampled'], bool): return False
    for field in ('today', 'week'):
        period = value.get(field)
        if period is None:
            if value['available']: return False
            continue
        if not isinstance(period, dict): return False
        if value['available'] and type(period.get('requests')) is not int: return False
        for count in ('requests', 'tokens'):
            n = period.get(count)
            if n is not None and (type(n) is not int or not 0 <= n <= 2**63-1): return False
        if period.get('amount') is not None and number(period['amount']) is None: return False
    models = value.get('models', [])
    if not isinstance(models, list) or len(models) > 3: return False
    return all(isinstance(model, dict) and isinstance(model.get('name'), str)
               and type(model.get('requests')) is int and 0 <= model['requests'] <= 5000 for model in models)


def site_analysis(provider, site, *, force=False):
    cc = ccswitch_module()
    directory = Path(os.environ.get('AI_QUOTA_DATA_DIR', str(Path.home()/'.swiftbar')))
    path = directory / 'ccswitch_analysis.json'
    day = datetime.now().astimezone().date().isoformat()
    try:
        saved = json.loads(path.read_text())
        if (isinstance(saved, dict) and valid_analysis(saved.get('analysis'))
                and number(saved.get('checked_at')) is not None
                and not force and saved.get('signature') == provider['source_signature']
                and saved.get('day') == day and 0 <= time.time() - saved.get('checked_at',0) < 300):
            return saved['analysis']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        payload = request_json('/api/log/token', provider['api_key'], origin=provider['origin'])
        rows = payload.get('data') if isinstance(payload, dict) and payload.get('success') is True else None
        if not isinstance(rows, list): raise ValueError()
        records = []
        for row in rows[:1000]:
            if not isinstance(row, dict) or row.get('type') != 2: continue
            incoming = number(row.get('prompt_tokens')); outgoing = number(row.get('completion_tokens'))
            records.append({'time':row.get('created_at'),'model':row.get('model_name'), 'cost':row.get('quota'),
                            'tokens':incoming+outgoing if incoming is not None and outgoing is not None and incoming>=0 and outgoing>=0 else None})
        data = site.get('data',{}) if isinstance(site,dict) and site.get('success') is True else {}
        divisor = number(data.get('quota_per_unit')) if isinstance(data,dict) else None
        usd = divisor is not None and divisor > 0 and data.get('quota_display_type') == 'USD'
        analysis = cc.summarize(records,source='站点当前 Key 日志（返回记录内统计，非完整账单）',
                                unit='站内 USD' if usd else '额度单位',divisor=divisor if usd else 1,limit=1000)
        analysis['returned_count'] = min(len(rows),1000)
        analysis['sampled'] = len(rows) >= 1000
    except Exception:
        analysis = cc.local_analysis(provider)
        if not analysis.get('available'):
            analysis['message'] = '站点未返回可用日志；' + analysis.get('message','CC Switch 暂无本机记录')
    # Cache aggregates only; never retain request logs, credentials, or response content.
    try:
        directory.mkdir(parents=True,exist_ok=True)
        fd,tmp = tempfile.mkstemp(dir=directory,prefix='.analysis-',suffix='.tmp')
        try:
            with os.fdopen(fd,'w') as stream:
                json.dump(dict(signature=provider['source_signature'],day=day,checked_at=time.time(),analysis=analysis),stream,ensure_ascii=False,allow_nan=False)
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    except (OSError,ValueError):pass
    return analysis


def fetch_ccswitch(cfg, previous=None, force=False):
    cc = ccswitch_module()
    try:
        provider = cc.read_current(cfg.get('ccswitch_app','claude'))
    except cc.SourceError as error:
        return dict(empty_result(error.code,error.message),dashboard_url=None,source_label='CC Switch 当前提供商')
    identity = dict(provider_name=provider['name'],provider_app=provider['app'],
                    source_signature=provider['source_signature'],source_label='CC Switch 当前提供商',
                    dashboard_url=provider['origin'],provider_origin=provider['origin'])
    if isinstance(previous,dict) and not force and previous.get('source_signature') == provider['source_signature']:
        retry = number(previous.get('next_retry_at'))
        if retry is not None and 0 < retry-time.time() <= 300:
            return previous
    try:
        # Probe only public service metadata before choosing a known read-only protocol.
        try: site = request_json('/api/status', origin=provider['origin'])
        except Exception: site = None
        new_api = isinstance(site,dict) and site.get('success') is True and isinstance(site.get('data'),dict) and 'quota_per_unit' in site['data']
        if new_api:
            token = provider.get('access_token'); user = provider.get('user_id')
            if isinstance(token,str) and token and not any(c.isspace() for c in token) and str(user).isdigit():
                payload = request_json('/api/user/self',token,user,origin=provider['origin'])
                value = parse_account(payload,site)
            else:
                value = parse_usage(request_json('/api/usage/token',provider['api_key'],origin=provider['origin']),site)
            identity['dashboard_url'] = provider['origin']+'/dashboard/overview'
        else:
            value = parse_sub2api(request_json('/v1/usage',provider['api_key'],origin=provider['origin']))
        value.update(identity)
        value['analysis'] = site_analysis(provider,site,force=force) if new_api else cc.local_analysis(provider)
        return value
    except urllib.error.HTTPError as error:
        value=empty_result('request_failed','当前中转站用量接口返回 HTTP %s；未沿用上一站点的数据' % error.code)
        value['http_status'] = error.code
    except Exception:
        value=empty_result('connection_failed','当前中转站查询失败或超时，稍后重试')
    value.update(identity)
    value['analysis'] = cc.local_analysis(provider)
    return value
