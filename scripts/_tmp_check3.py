import paramiko, os
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

script = '''
import json, sys
with open("/opt/lobster-server/logs/app.log") as f:
    lines = f.readlines()
for line in reversed(lines):
    if "18:39" in line and "sutui-exchange" in line:
        idx = line.find("{")
        if idx < 0:
            continue
        try:
            body = json.loads(line[idx:])
        except:
            continue
        msgs = body.get("messages", [])
        for m in msgs:
            if m.get("role") == "system":
                c = m.get("content", "")
                print("SYS_LEN:", len(c))
                print("FIRST500:", c[:500])
                print("HAS_FLUX:", "flux" in c)
                print("HAS_JIMENG:", "jimeng" in c)
                sys.exit(0)
        print("NO_SYS_MSG, msgs_count:", len(msgs))
        sys.exit(0)
print("NO_MATCH")
'''

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(ip, username=user, password=pw, timeout=10)

sftp = client.open_sftp()
with sftp.file("/tmp/_check_prompt.py", "w") as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = client.exec_command("python3 /tmp/_check_prompt.py", timeout=30)
out = stdout.read().decode("utf-8", errors="replace")
err = stderr.read().decode("utf-8", errors="replace")
print(out)
if err.strip():
    print("STDERR:", err[:300])
client.close()
