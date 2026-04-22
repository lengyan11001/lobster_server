#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from ssh_run_remote_pw import load_env_deploy
import paramiko

env = load_env_deploy()
host_str = env.get("LOBSTER_DEPLOY_HOST", "")
password = env.get("LOBSTER_DEPLOY_PASSWORD", "")
user, host = host_str.split("@", 1) if "@" in host_str else ("ubuntu", host_str)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(host, username=user, password=password, timeout=15)

def run(cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    return stdout.read().decode("utf-8", errors="replace")

print("=== MCP journal (last 5 min, grep for invoke/comfly/veo/redirect) ===")
out = run("sudo journalctl -u lobster-mcp --since '5 minutes ago' --no-pager 2>/dev/null | grep -iE 'invoke|comfly|veo|redirect|video.generate|daihuo|tool|WARNING' | tail -50")
print(out if out.strip() else "(no matches)")

print("\n=== Backend journal (last 5 min, grep for tool_call/invoke/comfly/veo) ===")
out = run("sudo journalctl -u lobster-backend --since '5 minutes ago' --no-pager 2>/dev/null | grep -iE 'invoke|comfly|veo|tool_call|CHAT|capability' | tail -50")
print(out if out.strip() else "(no matches)")

print("\n=== MCP log file (grep comfly/veo/redirect) ===")
out = run("grep -iE 'invoke|comfly|veo|redirect|daihuo|WARNING' /opt/lobster-server/mcp.log 2>/dev/null | tail -30")
print(out if out.strip() else "(no matches)")

client.close()
