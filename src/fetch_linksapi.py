#!/usr/bin/env python3
"""Read-only LinksAPI token usage. No inference calls, no credential logging."""
import json
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
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None

def request_json(path, key=None, user_id=None):
    if path not in ('/api/status', '/api/usage/token', '/api/user/self'):
        raise ValueError('Unsupported endpoint')
    # The live LinksAPI route canonicalizes this endpoint with a trailing slash.
    if path == '/api/usage/token':
        path += '/'
    headers = {'Accept': 'application/json', 'User-Agent': 'AIQuotaDashboard/6'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    if user_id is not None:
        if not str(user_id).isdigit():
            raise ValueError('Invalid user ID')
        headers['New-Api-User'] = str(user_id)
    request = urllib.request.Request(ORIGIN + path, headers=headers, method='GET')
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=4) as response:
        return json.load(response)

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
    site_data = site.get('data', {}) if isinstance(site, dict) and site.get('success') is True else {}
    divisor = number(site_data.get('quota_per_unit'))
    in_usd = bool(divisor and divisor > 0 and site_data.get('quota_display_type') == 'USD')
    divisor = divisor if in_usd else 1
    rem_pct = None
    if not unlimited and granted is not None and granted > 0:
        rem_pct = max(0, min(100, round(remaining / granted * 100)))
    limits = data.get('model_limits')
    restricted = data.get('model_limits_enabled') is True
    claude_allowed = None
    if restricted and isinstance(limits, dict):
        claude_allowed = any('claude' in str(k).lower() and v is True for k, v in limits.items())
    return dict(ok=True, state='usage_only' if unlimited else 'ready',
                status_msg='仅用量已同步，账户余额待配置' if unlimited else '已同步',
                balance_available=not unlimited, token_name=str(data.get('name') or '未命名令牌')[:100],
                scope='API 令牌额度', unlimited=unlimited, remaining_pct=rem_pct,
                remaining_amount=None if unlimited else remaining / divisor,
                used_amount=used / divisor if used is not None else None,
                granted_amount=None if unlimited or granted is None else granted / divisor,
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


def fetch_quota(cfg):
    if not cfg.get('enabled', False):
        return empty_result('not_configured', '已预留 · 在设置中配置后启用网络查询')
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
