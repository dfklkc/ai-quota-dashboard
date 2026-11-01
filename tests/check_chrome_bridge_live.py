"""Live regression: a 350 ms Chrome response must survive Apple Event timeout.
Requires Chrome and an open chatgpt.com tab; does not read a session or credentials.
Run manually after building the bridge.
"""
import json
from pathlib import Path
import subprocess
import time

helper = Path('/Applications/AI 桌面卡片.app/Contents/MacOS/ChromeSessionBridge')
start = '(()=>{const end=Date.now()+350;while(Date.now()<end){};return "started"})()'
poll = 'JSON.stringify({code:"probe_complete"})'
began = time.monotonic()
response = subprocess.run([str(helper), start, poll], capture_output=True, text=True, timeout=11)
rows = [json.loads(line) for line in response.stdout.splitlines() if line.strip()]
assert response.returncode == 0, 'Bridge process failed'
assert any(row.get('code') == 'probe_complete' for row in rows), 'Slow valid response was lost'
assert not any(row.get('error_number') == -1712 for row in rows), 'Apple Event timeout is too short'
print('Chrome slow-response regression passed (%.2f s)' % (time.monotonic() - began))
