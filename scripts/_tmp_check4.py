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
# Find the latest stream=true request that has messages and tools (OpenClaw LLM call)
for line in reversed(lines):
    if "18:39" not in line:
        continue
    if "sutui-exchange" not in line:
        continue
    idx = line.find("{")
    if idx < 0:
        continue
    try:
        body = json.loads(line[idx:])
    except:
        continue
    keys = list(body.keys())[:10]
    has_msgs = "messages" in body
    has_tools = "tools" in body
    msg_count = len(body.get("messages", []))
    tool_count = len(body.get("tools", []))
    print(f"Keys: {keys}")
    print(f"has_messages: {has_msgs} count: {msg_count}")
    print(f"has_tools: {has_tools} count: {tool_count}")
    if has_msgs and msg_count > 0:
        for m in body["messages"]:
            role = m.get("role", "?")
            content = m.get("content", "")
            content_len = len(content) if content else 0
            tool_calls = m.get("tool_calls", [])
            print(f"  msg role={role} content_len={content_len} tool_calls={len(tool_calls)}")
            if role == "system" and content:
                print(f"  SYSTEM first 500: {content[:500]}")
                print(f"  HAS flux: {'flux' in content}")
                print(f"  HAS jimeng: {'jimeng' in content}")
        break
    elif has_tools and tool_count > 0:
        # Check if choices contain tool calls
        choices = body.get("choices", [])
        if choices:
            print("This is a response, not a request. Skipping.")
            continue
        print("Request without messages but with tools.")
        break
    else:
        print("Continuing to previous line...")
        continue
'''

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(ip, username=user, password=pw, timeout=10)
sftp = client.open_sftp()
with sftp.file("/tmp/_check_prompt.py", "w") as f:
    f.write(script)
sftp.close()
stdin, stdout, stderr = client.exec_command("python3 /tmp/_check_prompt.py", timeout=30)
print(stdout.read().decode("utf-8", errors="replace"))
err = stderr.read().decode("utf-8", errors="replace")
if err.strip():
    print("STDERR:", err[:300])
client.close()
