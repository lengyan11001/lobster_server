import json, re, paramiko, os
from pathlib import Path
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

env = {}
for line in Path(".env.deploy").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

host = env.get("LOBSTER_DEPLOY_HOST", "")
pw = env.get("LOBSTER_DEPLOY_PASSWORD", "")
user, ip = host.split("@") if "@" in host else ("ubuntu", host)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(ip, username=user, password=pw, timeout=10)

cmd = r"""python3 -c "
import json, sys
with open('/opt/lobster-server/logs/app.log') as f:
    lines = f.readlines()
# Find latest sutui-exchange line
for line in reversed(lines):
    if '18:39:16' in line and 'sutui-exchange' in line:
        idx = line.find('{')
        if idx < 0: continue
        try:
            body = json.loads(line[idx:])
        except: continue
        for m in body.get('messages', []):
            if m.get('role') == 'system':
                c = m.get('content', '')
                print('SYSTEM PROMPT (first 2000 chars):')
                print(c[:2000])
                print('---')
                print('Total length:', len(c))
                break
        break
"
"""
stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
print(stdout.read().decode("utf-8", errors="replace"))
err = stderr.read().decode("utf-8", errors="replace")
if err.strip():
    print("STDERR:", err[:500])
client.close()
