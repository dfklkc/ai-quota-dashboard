#!/usr/bin/env python3
"""Read existing account quotas. Never replace unknown usage with a full bucket."""
import base64
import argparse
import concurrent.futures
import fcntl
import json
import math
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = Path(os.environ.get('AI_QUOTA_DATA_DIR', str(Path.home() / '.swiftbar')))
CONFIG_FILE = ROOT / 'ai_accounts_config.json'
GPT_ACCOUNTS_CACHE = ROOT / 'gpt_accounts_quota.json'
AGY_CACHE = ROOT / 'antigravity_quota.json'
USER_AGENT = 'Mozilla/5.0 AIQuotaDashboard/2'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A usage query must never forward account credentials to a redirect target.
        raise urllib.error.HTTPError(req.full_url, code, 'Redirect refused', headers, fp)


def open_usage(request, timeout):
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def atomic_json(path, data):
    fd, tmp = tempfile.mkstemp(dir=ROOT, prefix='.quota-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def percent(value):
    value = finite_number(value)
    if value is None:
        return None
    return max(0, min(100, int(round(value))))


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def window_minutes(value):
    seconds = finite_number(value)
    return seconds / 60 if seconds is not None and 0 < seconds <= 2 ** 63 - 1 else None


def nonnegative_integer(value):
    number = finite_number(value)
    if number is None or number < 0 or number > 2 ** 63 - 1 or int(number) != number:
        return None
    return int(number)


def retry_pending(previous):
    retry_at = finite_number(previous.get('next_retry_at'))
    if retry_at is None:
        return False
    # Corrupt timestamps or a backward clock adjustment must not block a source forever.
    return 0 < retry_at - time.time() <= 300


def quota_window(window, used_key, reset_key, fraction=False):
    window = window if isinstance(window, dict) else {}
    value = window.get(used_key)
    if fraction:
        value = finite_number(value)
        value = value * 100 if value is not None else None
    p = percent(value)
    remaining = p if fraction else (100 - p if p is not None else None)
    ts = window.get(reset_key)
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
        except (ValueError, OverflowError, OSError):
            ts = None
    ts = finite_number(ts)
    # Swift converts the reset delta to Int; reject even finite values outside a calendar timestamp.
    if ts is not None and not 0 < ts <= 253402300799:
        ts = None
    return remaining, (100 - remaining if remaining is not None else None), ts


def sync_chrome_account(cfg):
    # Imported lazily so quota parsing remains independent from browser synchronization.
    import importlib.util
    spec = importlib.util.spec_from_file_location('local_quota_binding', Path(__file__).with_name('bind_account.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.sync_account(cfg)


def apply_usage(result, data):
    limits = data.get('rate_limit') or {}
    limits = limits if isinstance(limits, dict) else {}
    p = limits.get('primary_window')
    s = limits.get('secondary_window')
    p = p if isinstance(p, dict) else {}
    s = s if isinstance(s, dict) else {}
    pr, pu, pt = quota_window(p, 'used_percent', 'reset_at')
    sr, su, st = quota_window(s, 'used_percent', 'reset_at')
    if pr is None and sr is None:
        result['status_msg'] = '接口未提供额度窗口'
        return result
    plan = data.get('plan_type')
    plan = plan.strip() if isinstance(plan, str) else ''
    reset_credits = data.get('rate_limit_reset_credits')
    reset_credits = reset_credits if isinstance(reset_credits, dict) else {}
    result.update(ok=True, connected=True, status_msg='已同步', plan=plan or '已连接',
                  p_rem=pr, p_used=pu, p_reset_at=pt, s_rem=sr, s_used=su, s_reset_at=st,
                  p_window_mins=window_minutes(p.get('limit_window_seconds')),
                  s_window_mins=window_minutes(s.get('limit_window_seconds')),
                  free_resets=nonnegative_integer(reset_credits.get('available_count')),
                  source='cloud_https', updated_at=now_iso(), last_success_at=now_iso())
    return result


def query_account_quota(cfg, old=None):
    follow = cfg.get('follow_current_session', False)
    result = {} if follow else dict(old or {})
    result.update(id=cfg['id'], name=cfg.get('name', cfg['id']), email=cfg.get('email', ''),
                  ok=False, connected=False, status_msg='未同步', source='none', updated_at=now_iso())
    if not result.get('last_success_at'):
        for k in ('p_rem', 'p_used', 's_rem', 's_used', 'free_resets'):
            result[k] = None
    auto = cfg.get('type') == 'token_file' and cfg.get('auto_sync_chrome', False)

    def synchronize():
        bound = sync_chrome_account(cfg)
        if bound.get('ok'):
            result['token_sync_msg'] = 'Chrome 已自动同步'
            return apply_usage(result, bound['usage'])
        result['status_msg'] = bound.get('message', 'Chrome 凭证同步失败')
        result['token_sync_msg'] = result['status_msg']
        return result

    # Always consult the browser before using a web credential in follow mode.
    if follow and cfg.get('type') == 'token_file':
        bound = sync_chrome_account(cfg)
        result['email'] = bound.get('email', '')
        result['source_label'] = bound.get('source_label', 'Chrome 当前会话')
        result['detected_accounts'] = bound.get('detected_accounts', 0)
        if bound.get('ok'):
            return apply_usage(result, bound['usage'])
        result['status_msg'] = bound.get('message', 'Chrome 会话读取失败')
        return result

    try:
        auth = read_json(os.path.expanduser(cfg.get('auth_file', '')), {})
        tokens = auth.get('tokens') or auth
        token = tokens.get('access_token')
        if not token:
            if auto:
                return synchronize()
            result['status_msg'] = '缺少登录凭证，请在账号配置中更新'
            return result
        identity = auth.get('email')
        expiry = None
        for encoded in (tokens.get('id_token'), token):
            try:
                payload = encoded.split('.')[1]
                claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
                identity = claims.get('email') or (claims.get('https://api.openai.com/profile') or {}).get('email') or identity
                if encoded == token:
                    expiry = claims.get('exp')
            except Exception:
                pass
        if follow:
            result['email'] = identity or ''
        expected = '' if follow else cfg.get('email', '').strip().lower()
        if expected and identity and expected != identity.strip().lower():
            if auto:
                return synchronize()
            result['status_msg'] = '登录账号与配置邮箱不一致，请检查配置'
            return result
        # Renew shortly before expiry while the browser session is available.
        # If renewal fails, continue using a still-valid token until it expires.
        if auto and isinstance(expiry, (int, float)) and expiry < time.time() + 300:
            bound = synchronize()
            if bound.get('ok') or expiry <= time.time():
                return bound
        headers = {'Authorization': 'Bearer ' + token, 'User-Agent': USER_AGENT}
        if tokens.get('account_id'):
            headers['ChatGPT-Account-Id'] = tokens['account_id']
        req = urllib.request.Request('https://chatgpt.com/backend-api/wham/usage', headers=headers)
        with open_usage(req, timeout=5) as resp:
            data = json.load(resp)
        return apply_usage(result, data)
    except urllib.error.HTTPError as e:
        if e.code == 401 and auto:
            return synchronize()
        result['status_msg'] = ('登录已过期，请重新登录 (401)' if e.code == 401 else
                                '请求过于频繁，请稍后刷新 (429)' if e.code == 429 else
                                '接口返回 HTTP %s' % e.code)
    except Exception:
        result['status_msg'] = '连接失败或超时，等待重试'
    return result


def record_attempt(result, old):
    old = old if isinstance(old, dict) else {}
    prior_failures = nonnegative_integer(old.get('failure_count')) or 0
    failures = 0 if result.get('ok') else min(prior_failures + 1, 2 ** 31 - 1)
    result['failure_count'] = failures
    result['next_retry_at'] = time.time() + min(300, 60 * 2 ** min(failures - 1, 3)) if failures else 0
    return result


def fetch_all_gpt_accounts(config, force=False, source=None):
    accounts = [a for a in config.get('chatgpt_accounts', []) if a.get('enabled', True)]
    if not accounts:
        return
    cached = read_json(GPT_ACCOUNTS_CACHE, [])
    cached = cached if isinstance(cached, list) else []
    old = {}
    for item in cached:
        if not isinstance(item, dict) or 'id' not in item:
            continue
        item = dict(item)
        if item.get('plan') is not None and not isinstance(item['plan'], str):
            item['plan'] = None
        if 'free_resets' in item:
            item['free_resets'] = nonnegative_integer(item['free_resets'])
        old[item['id']] = item
    results = {a['id']: old[a['id']] for a in accounts if a['id'] in old}
    last_saved = cached
    def save():
        nonlocal last_saved
        payload = [results[a['id']] for a in accounts if a['id'] in results]
        if payload != last_saved:
            atomic_json(GPT_ACCOUNTS_CACHE, payload)
            last_saved = payload
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        for cfg in accounts:
            previous = old.get(cfg['id'], {})
            if source and source != cfg['id']:
                continue
            if not force and retry_pending(previous):
                continue
            futures[pool.submit(query_account_quota, cfg, previous)] = cfg
        for future in concurrent.futures.as_completed(futures):
            cfg = futures[future]
            try:
                item = future.result()
            except Exception:
                item = dict(id=cfg['id'], name=cfg.get('name'), email='', ok=False,
                            status_msg='读取异常，请重试', updated_at=now_iso())
            results[cfg['id']] = record_attempt(item, old.get(cfg['id']))
            save()  # Each completed account becomes visible immediately.
    save()


def retrieve_agy_summary():
    processes = subprocess.run(['ps', '-axo', 'pid=,args='], capture_output=True, text=True, timeout=2).stdout
    for line in processes.splitlines():
        if 'language_server' not in line or '--csrf_token' not in line:
            continue
        match = re.search(r'--csrf_token(?:=|\s+)([^\s]+)', line)
        if not match:
            continue
        pid = line.split()[0]
        ports = subprocess.run(['/usr/sbin/lsof', '-a', '-p', pid, '-iTCP', '-sTCP:LISTEN', '-Fn'],
                               capture_output=True, text=True, timeout=2).stdout
        for port in dict.fromkeys(re.findall(r'^n.*:(\d+)$', ports, re.M)):
            # Antigravity's existing local RPC uses its self-signed loopback certificate.
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(
                'https://127.0.0.1:%s/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary' % port,
                data=b'{}', headers={'Content-Type': 'application/json', 'x-codeium-csrf-token': match[1]})
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=2) as resp:
                    data = json.load(resp)
                if isinstance(data.get('response', {}).get('groups'), list):
                    return data['response']
            except Exception:
                continue
    return None


def parse_agy_summary(summary, cfg):
    result = {'ok': False, 'is_running': True, 'status_msg': '服务已连接，未提供额度窗口', 'email': cfg.get('email', ''),
              'updated_at': now_iso(), 'last_success_at': now_iso()}
    for model in ('gemini', 'claude'):
        group = next((g for g in summary.get('groups', []) if model in g.get('displayName', '').lower()), {})
        for window, key in [('5h', '5h'), ('weekly', 'weekly')]:
            bucket = next((b for b in group.get('buckets', []) if b.get('window') == window), {})
            rem, used, reset = quota_window(bucket, 'remainingFraction', 'resetTime', fraction=True)
            result[model + '_' + key + '_rem'] = rem
            result[model + '_' + key + '_used'] = used
            result[model + '_' + key + '_reset_at'] = reset
    result['ok'] = any(result.get(m + '_' + w + '_rem') is not None for m in ('gemini', 'claude') for w in ('5h', 'weekly'))
    if result['ok']:
        result['status_msg'] = '已同步'
    else:
        result.pop('last_success_at', None)
    return result


def fetch_antigravity_quota(config, force=False):
    cfg = config.get('antigravity', {})
    if not cfg.get('enabled', True):
        return
    result = read_json(AGY_CACHE, {})
    result = result if isinstance(result, dict) else {}
    old = dict(result)
    if not force and retry_pending(result):
        return
    try:
        summary = retrieve_agy_summary()
        if summary is None:
            raise ValueError('unavailable')
        result = parse_agy_summary(summary, cfg)
    except Exception:
        result.update(ok=False, is_running=False, status_msg='额度服务未连接', updated_at=now_iso())
    atomic_json(AGY_CACHE, record_attempt(result, old))


def query_linksapi_quota(cfg, old=None, force=False):
    import importlib.util
    spec = importlib.util.spec_from_file_location('local_linksapi', Path(__file__).with_name('fetch_linksapi.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.fetch_quota(cfg, previous=old, force=force) if cfg.get("credential_source") == "ccswitch" else module.fetch_quota(cfg)


def fetch_linksapi_quota(config, force=False):
    cfg = config.get('linksapi', {})
    if not cfg.get('enabled', False):
        return
    path = ROOT / 'linksapi_quota.json'
    old = read_json(path, {})
    old = old if isinstance(old, dict) else {}
    if cfg.get('credential_source') != 'ccswitch' and not force and retry_pending(old):
        return
    result = query_linksapi_quota(cfg, old, force) if cfg.get('credential_source') == 'ccswitch' else query_linksapi_quota(cfg)
    if result is old:
        return
    prior = {} if cfg.get('credential_source') == 'ccswitch' and result.get('source_signature') != old.get('source_signature') else old
    recorded = record_attempt(result, prior)
    if cfg.get('credential_source') == 'ccswitch' and (recorded.get('ok') or recorded.get('http_status') == 429):
        recorded['next_retry_at'] = time.time() + 300
    atomic_json(path, recorded)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--source')
    args = parser.parse_args()
    # Prevent overlap across GUI restarts and manual invocations.
    with (ROOT / '.fetch.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(75)
        config = read_json(CONFIG_FILE, None)
        if not isinstance(config, dict):
            raise SystemExit('账号配置无法读取')
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            jobs = []
            accounts = [a for a in config.get('chatgpt_accounts', []) if a.get('enabled', True)]
            if args.source not in ('antigravity', 'linksapi') and any(args.source is None or args.source == a.get('id') for a in accounts):
                jobs.append(pool.submit(fetch_all_gpt_accounts, config, args.force, args.source))
            if (args.source is None or args.source == 'antigravity') and config.get('antigravity', {}).get('enabled', True):
                jobs.append(pool.submit(fetch_antigravity_quota, config, args.force))
            if (args.source is None or args.source == 'linksapi') and config.get('linksapi', {}).get('enabled', False):
                jobs.append(pool.submit(fetch_linksapi_quota, config, args.force))
            for job in jobs:
                job.result()


if __name__ == '__main__':
    main()
