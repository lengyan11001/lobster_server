import json, re, subprocess, os, sys
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ".")
from pathlib import Path

env_path = Path(".env.deploy")
env = {}
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

host = env.get("LOBSTER_DEPLOY_HOST", "")
pw = env.get("LOBSTER_DEPLOY_PASSWORD", "")

import paramiko
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
user, ip = host.split("@") if "@" in host else ("ubuntu", host)
client.connect(ip, username=user, password=pw, timeout=10)

cmd = r"""python3 -c "
import json, re, sys
with open('/opt/lobster-server/logs/app.log') as f:
    for line in f:
        if '18:39:16' in line and 'sutui-exchange' in line:
            idx = line.find('{')
            if idx < 0: continue
            try:
                body = json.loads(line[idx:])
            except: continue
            for m in body.get('messages', []):
                if m.get('role') == 'system':
                    c = m.get('content', '')
                    for match in re.findall(r'image.generate.{0,150}', c)[:3]:
                        print('MATCH:', match)
                    print('HAS_FLUX:', 'flux' in c.lower())
                    print('HAS_JIMENG:', 'jimeng' in c.lower())
                    print('HAS_BAN:', chr(31105)+chr(27490)+chr(33258)+chr(34892)+chr(36873)+chr(25321) in c)
                    break
            break
"
"""
stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
print(stdout.read().decode("utf-8", errors="replace"))
err = stderr.read().decode("utf-8", errors="replace")
if err:
    print("STDERR:", err)
client.close()
