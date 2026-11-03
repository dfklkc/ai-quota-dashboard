"""Read the selected CC Switch provider and bounded usage aggregates, never modify its database."""
from contextlib import closing
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import sqlite3
import time
import urllib.parse
from datetime import datetime, timedelta
try:
    import tomllib
except ImportError:  # The distributed app includes Python 3.13.
    tomllib = None

CC_ROOT = Path.home() / '.cc-switch'
CLIENTS = {'claude': 'currentProviderClaude', 'claude-desktop': 'currentProviderClaudeDesktop',
           'codex': 'currentProviderCodex', 'gemini': 'currentProviderGemini'}

class SourceError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def origin_url(value):
    if not isinstance(value, str):
        raise SourceError('invalid_origin', 'CC Switch 未提供可用的中转站地址')
    try:
        u = urllib.parse.urlsplit(value)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment or u.port not in (None, 443):
            raise ValueError()
        host = u.hostname.lower()
        if host == 'localhost' or host.endswith(('.localhost', '.local')) or '.' not in host:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError()
        return 'https://' + host
    except (ValueError, AttributeError):
        raise SourceError('invalid_origin', '仅查询 CC Switch 中已配置的 HTTPS 公网中转站，不向本机代理或其他站点转发凭证')


def _object(raw):
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def read_current(app='claude', root=None):
    root = Path(root) if root is not None else CC_ROOT
    if app not in CLIENTS:
        raise SourceError('invalid_client', '不支持此 CC Switch 客户端类型')
    try:
        settings = json.loads((root / 'settings.json').read_text()) if (root / 'settings.json').exists() else {}
        if not isinstance(settings,dict): raise ValueError()
        selected = settings.get(CLIENTS[app])
        with closing(sqlite3.connect((root / 'cc-switch.db').resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            db.row_factory = sqlite3.Row
            if selected:
                rows = db.execute('SELECT id,name,settings_config,meta,website_url,category FROM providers WHERE app_type=? AND id=?', (app, selected)).fetchall()
            else:
                rows = db.execute('SELECT id,name,settings_config,meta,website_url,category FROM providers WHERE app_type=? AND is_current=1', (app,)).fetchall()
        if len(rows) != 1:
            raise SourceError('no_provider', 'CC Switch 尚未选定唯一的当前提供商')
        row = dict(rows[0])
    except (OSError, sqlite3.Error, ValueError):
        raise SourceError('ccswitch_unavailable', '无法读取 CC Switch 配置，请确认已安装并配置提供商')
    config = _object(row['settings_config']); meta = _object(row['meta'])
    env = config.get('env') if isinstance(config.get('env'), dict) else {}
    auth = config.get('auth') if isinstance(config.get('auth'), dict) else {}
    base = key = None
    if app in ('claude', 'claude-desktop'):
        base = env.get('ANTHROPIC_BASE_URL')
        key = env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY')
    elif app == 'gemini':
        base = env.get('GOOGLE_GEMINI_BASE_URL') or env.get('GEMINI_API_BASE_URL')
        key = env.get('GEMINI_API_KEY') or env.get('GOOGLE_API_KEY')
    elif app == 'codex':
        base = env.get('OPENAI_BASE_URL')
        key = auth.get('OPENAI_API_KEY') or env.get('OPENAI_API_KEY')
        if isinstance(config.get('config'), str) and config['config'].strip():
            if tomllib is None:
                raise SourceError('unsupported_config', '此配置需要应用内置的 TOML 解析器')
            try:
                document = tomllib.loads(config['config'])
                provider = (document.get('model_providers') or {}).get(document.get('model_provider'), {})
                base = provider.get('base_url') or document.get('base_url') or base
                key = key or provider.get('experimental_bearer_token') or provider.get('api_key') or document.get('api_key')
            except (ValueError, TypeError, AttributeError):
                raise SourceError('unsupported_config', 'CC Switch 当前提供商的配置格式无法解析')
    if not base or not key:
        raise SourceError('not_relay', '当前提供商未提供中转地址和 API Key；官方订阅额度请查看上方账号卡片')
    if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key):
        raise SourceError('missing_key', 'CC Switch 当前提供商缺少有效的 API Key')
    origin = origin_url(base)
    if urllib.parse.urlsplit(base).path.rstrip('/') not in ('', '/v1', '/v1beta'):
        raise SourceError('unsupported_path', '此站点使用特殊 API 路径，暂不推测其用量接口')
    usage = meta.get('usage_script') or meta.get('usageScript') or {}
    usage = usage if isinstance(usage, dict) else {}
    usage_base = usage.get('baseUrl') or usage.get('base_url')
    if usage_base and origin_url(usage_base) != origin:
        raise SourceError('origin_mismatch', 'CC Switch 的查询地址与当前中转站不同，已停止发送凭证')
    access = usage.get('accessToken') or usage.get('access_token')
    user = usage.get('userId') or usage.get('user_id')
    template = str(usage.get('templateType') or usage.get('template_type') or '').lower()
    digest = hashlib.sha256(json.dumps([app, row['id'], row['name'], config, usage], sort_keys=True).encode()).hexdigest()
    return {'id': row['id'], 'app': app, 'name': str(row['name'])[:100], 'origin': origin,
            'api_key': key, 'access_token': access, 'user_id': user, 'template': template,
            'source_signature': digest}


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) and abs(value) < 1e18 else None
    except OverflowError:
        return None


def summarize(records, *, source, unit, divisor=1, now=None, limit=None):
    now = now or datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = start - timedelta(days=6)
    periods = {name: {'requests': 0, 'tokens': 0, 'amount': 0.0, 'amount_complete': True, 'tokens_complete': True}
               for name in ('today', 'week')}
    models = {}; accepted = 0
    for row in records:
        ts = finite(row.get('time'))
        if ts is None or ts <= 0 or ts > now.timestamp() + 60:
            continue
        cost = finite(row.get('cost'))
        tokens = finite(row.get('tokens'))
        if tokens is None or tokens < 0 or int(tokens) != tokens:
            tokens = None
        accepted += 1
        for name, begin in [('today', start), ('week', week)]:
            if ts < begin.timestamp(): continue
            period = periods[name];period['requests'] += 1
            if tokens is not None and period['tokens'] + tokens <= 2**63-1: period['tokens'] += int(tokens)
            else: period['tokens_complete'] = False
            if cost is None or cost < 0: period['amount_complete'] = False
            else:
                amount = finite(cost / divisor) if finite(divisor) is not None and divisor > 0 else None
                total = finite(period['amount'] + amount) if amount is not None else None
                if total is None: period['amount_complete'] = False
                else: period['amount'] = total
        model = str(row.get('model') or '未提供模型')[:80]
        models[model] = models.get(model, 0) + 1
    for period in periods.values():
        if not period.pop('amount_complete'): period['amount'] = None
        if not period.pop('tokens_complete'): period['tokens'] = None
    return dict(available=True, source=source, unit=unit, sample_count=accepted,
                sampled=limit is not None and len(records) >= limit,
                today=periods['today'], week=periods['week'],
                models=[{'name': key, 'requests': value} for key,value in sorted(models.items(), key=lambda x: (-x[1],x[0]))[:3]],
                updated_at=now.isoformat(timespec='seconds'))


def local_analysis(provider, root=None, now=None):
    root = Path(root) if root is not None else CC_ROOT
    now = now or datetime.now().astimezone()
    begin = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)).timestamp()
    try:
        with closing(sqlite3.connect((root/'cc-switch.db').resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON');db.row_factory=sqlite3.Row
            deadline=time.monotonic()+1
            db.set_progress_handler(lambda: time.monotonic()>deadline,1000)
            rows = db.execute('SELECT created_at,model,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens,total_cost_usd,status_code FROM proxy_request_logs WHERE app_type=? AND provider_id=? AND created_at>=? ORDER BY created_at DESC LIMIT 5000',
                              (provider['app'],provider['id'],begin)).fetchall()
        if not rows:
            return {'available': False, 'source': 'CC Switch 本机记录', 'message': '当前提供商没有近7日本机记录，不能据此推断站点实际用量'}
        records=[]
        for row in rows:
            try: cost=float(row['total_cost_usd'])
            except (ValueError,TypeError): cost=None
            tokens=[finite(row[k]) for k in ('input_tokens','output_tokens')]
            records.append({'time':row['created_at'],'model':row['model'],'cost':cost,'tokens':sum(tokens) if all(t is not None and t>=0 for t in tokens) else None})
        value=summarize(records,source='CC Switch 本机记录（估算费用，非站点账单）',unit='估算 USD',now=now,limit=5000)
        return value
    except (OSError,sqlite3.Error):
        return {'available':False,'source':'CC Switch 本机记录','message':'CC Switch 暂无可读取的请求统计表'}
