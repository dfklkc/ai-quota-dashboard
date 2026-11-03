#!/usr/bin/env python3
"""Synchronize the current or explicitly configured Chrome account; stdout never contains credentials."""
import base64
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = Path(os.environ.get('AI_QUOTA_DATA_DIR', str(Path.home() / '.swiftbar')))
CONFIG_FILE = ROOT / 'ai_accounts_config.json'
LAST_BROWSER_ERROR = ''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'Redirect refused', headers, fp)


def open_usage(request, timeout):
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)

MESSAGES = {
    'chrome_closed': 'Chrome 未运行，请打开并登录目标 ChatGPT 账号',
    'no_tab': 'Chrome 中没有 chatgpt.com 页面，请打开目标账号页面',
    'not_logged_in': 'ChatGPT 页面尚未登录或会话未就绪，请登录后重试',
    'javascript_disabled': 'Chrome 未允许 Apple 事件中的 JavaScript；请在「显示 → 开发者」中检查该选项',
    'automation_denied': 'macOS 未允许看板控制 Chrome；请检查「隐私与安全性 → 自动化」权限',
    'browser_error': 'Chrome 脚本读取失败，请重新加载 ChatGPT 页面后重试',
    'timeout': '读取 Chrome 会话超时，请重新加载 ChatGPT 页面后重试',
    'account_mismatch': 'Chrome 登录邮箱与此卡片绑定邮箱不一致，未覆盖凭证',
    'email_required': '请先保存此账号的绑定邮箱，再同步 Chrome 凭证',
    'unsupported': '此账号不使用网页版凭证，未更改客户端登录',
    'busy': '另一项凭证同步正在进行，请稍后重试',
    'storage_error': '凭证保存失败，请检查文件权限',
    'invalid_session': '读取到的会话缺少有效凭证，未覆盖原凭证',
    'validation_failed': '网页登录凭证暂时无法查询 Codex 额度，未覆盖原凭证',
    'success': 'Chrome 凭证已验证并同步，额度正在更新',
}


def result(code, **extra):
    data = dict(ok=code == 'success', code=code, message=MESSAGES.get(code, code))
    data.update(extra)
    return data


def session_scripts():
    start_js = '''(()=>{const k="__localQuotaSessionV4";
const c=new AbortController();const timeout=setTimeout(()=>c.abort(),2500);
window[k]={pending:true};setTimeout(()=>delete window[k],10000);fetch("/api/auth/session",{credentials:"same-origin",cache:"no-store",signal:c.signal})
.then(r=>r.json()).then(s=>{window[k]=s&&s.accessToken?{access_token:s.accessToken,email:s.user?.email||"",account_id:s.account?.id||""}:{code:"not_logged_in"}})
.catch(()=>{window[k]={code:"timeout"}}).finally(()=>clearTimeout(timeout));return "started"})()'''.replace('\n','')
    poll_js = '''(()=>{const k="__localQuotaSessionV4",s=window[k];if(!s||s.pending)return JSON.stringify({code:"pending"});delete window[k];return JSON.stringify(s)})()'''
    key = "__localQuota_" + uuid.uuid4().hex
    return tuple(s.replace("__localQuotaSessionV4", key) for s in (start_js, poll_js))


def get_chrome_sessions(first_session=False):
    global LAST_BROWSER_ERROR
    LAST_BROWSER_ERROR = ''
    try:
        helper = Path(os.environ.get('AI_QUOTA_APP_BUNDLE', str(ROOT / 'AIQuotaDashboard.app'))) / 'Contents/MacOS/ChromeSessionBridge'
        arguments = [str(helper), *session_scripts()]
        if first_session:
            arguments.append('--first-session')
        p = subprocess.run(arguments, capture_output=True, text=True, timeout=11)
    except subprocess.TimeoutExpired as exc:
        # A later slow tab must not discard an earlier successful session.
        output = exc.stdout or b''
        if isinstance(output, bytes):
            output = output.decode('utf-8', errors='replace')
        p = subprocess.CompletedProcess([], 0, output + '\n{"code":"timeout"}', '')
    except OSError:
        return [], 'browser_error'
    if p.returncode:
        return [], 'automation_denied' if '-1743' in p.stderr else 'browser_error'
    sessions, errors = [], []
    for line in p.stdout.splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            if isinstance(d.get('access_token'), str) and d.get('access_token'):
                sessions.append(d)
            else:
                errors.append(d.get('code', 'invalid_session'))
                if d.get('error_number') is not None:
                    LAST_BROWSER_ERROR = '（错误码 %s）' % d['error_number']
                elif d.get('exception'):
                    LAST_BROWSER_ERROR = '（%s）' % d['exception']
        except (ValueError, AttributeError):
            errors.append('invalid_session')
    return sessions, (errors[0] if errors else ('' if sessions else 'invalid_session'))


def jwt_claims(token):
    try:
        payload = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except Exception:
        return {}


def validate_session(session, expected):
    expected = expected.strip().lower()
    claims = jwt_claims(session.get('access_token', ''))
    claimed_email = claims.get('email') or (claims.get('https://api.openai.com/profile') or {}).get('email')
    actual = session.get('email', '').strip().lower()
    if not expected:
        return result('email_required')
    if actual != expected or (claimed_email and claimed_email.lower() != expected):
        return result('account_mismatch')
    token = session.get('access_token')
    if not isinstance(token, str) or not token:
        return result('invalid_session')
    account_id = session.get('account_id') or (claims.get('https://api.openai.com/auth') or {}).get('chatgpt_account_id')
    headers = {'Authorization': 'Bearer ' + token, 'User-Agent': 'Mozilla/5.0 AIQuotaDashboard/3'}
    if account_id:
        headers['ChatGPT-Account-Id'] = account_id
    try:
        req = urllib.request.Request('https://chatgpt.com/backend-api/wham/usage', headers=headers)
        with open_usage(req, timeout=4) as resp:
            usage = json.load(resp)
        windows = usage.get('rate_limit') or {}
        def valid_usage(window):
            value = window.get('used_percent') if isinstance(window, dict) else None
            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if not isinstance(windows, dict) or not any(valid_usage(windows.get(k)) for k in ['primary_window', 'secondary_window']):
            return result('validation_failed')
    except urllib.error.HTTPError as e:
        return result('validation_failed', message='网页登录凭证查询额度返回 HTTP %s，未覆盖原凭证' % e.code)
    except Exception:
        return result('validation_failed')
    return result('success', credentials={'access_token': token, 'email': actual,
                  'account_id': account_id or '', 'synced_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}, usage=usage)


def save_credentials(path, credentials):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.session-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(credentials, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def sync_account(cfg):
    if cfg.get('type') != 'token_file':
        return result('unsupported')
    follow = cfg.get('follow_current_session', False)
    if not follow and not cfg.get('email', '').strip():
        return result('email_required')
    if Path(cfg.get('auth_file', '')).expanduser().resolve() == (Path.home() / '.codex/auth.json').resolve():
        return result('unsupported')
    try:
        with (ROOT / '.token_sync.lock').open('w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return result('busy')
            sessions, error = get_chrome_sessions(first_session=follow)
            if not sessions:
                return result(error, message=MESSAGES.get(error, error) + LAST_BROWSER_ERROR)
            candidates = sessions if follow else [s for s in sessions if s.get('email', '').strip().lower() == cfg['email'].strip().lower()]
            if not candidates:
                return result('account_mismatch')
            unique = {(s.get('email', ''), s.get('account_id', '')) for s in sessions}
            current_email = candidates[0].get('email', '')
            checked = validate_session(candidates[0], current_email if follow else cfg['email'])
            checked['email'] = current_email
            checked['source_label'] = candidates[0].get('source_label', 'Chrome 当前会话')
            checked['detected_accounts'] = len(unique)
            if not checked['ok']:
                return checked
            credentials = checked.pop('credentials')
            if not follow:
                save_credentials(cfg['auth_file'], credentials)
            return checked
    except OSError:
        return result('storage_error')


def main():
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
        account = next(a for a in cfg['chatgpt_accounts'] if a['id'] == sys.argv[1])
        checked = sync_account(account)
    except Exception:
        checked = dict(ok=False, code='config_error', message='账号配置不存在或尚未保存，请先保存配置')
    # Return only safe diagnostics. Never log the browser response or credentials.
    public = {k: checked[k] for k in ['ok', 'code', 'message']}
    print(json.dumps(public, ensure_ascii=False))
    raise SystemExit(0 if public['ok'] else 1)


if __name__ == '__main__':
    main()
