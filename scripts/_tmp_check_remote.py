#!/usr/bin/env python3
"""Check remote repo state on test server."""
import paramiko

HOST = "139.199.168.36"
USER = "ubuntu"
PASSWORD = "Q9sb*z[7e?h2YCP"

def run_cmd(client, cmd, timeout=30):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode()
    err = stderr.read().decode()
    code = stdout.channel.recv_exit_status()
    return out, err, code

def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=15)

    checks = [
        ("Repo branch + remote", "cd /opt/lobster-server && git branch -a && echo '---' && git remote -v && echo '---' && git log --oneline -3"),
        ("Working tree status", "cd /opt/lobster-server && git status --short | head -20"),
        (".env exists?", "ls -la /opt/lobster-server/.env 2>/dev/null || echo NO_ENV_FILE"),
        (".venv exists?", "ls -la /opt/lobster-server/.venv/bin/python 2>/dev/null || echo NO_VENV"),
        ("requirements.txt", "head -5 /opt/lobster-server/requirements.txt 2>/dev/null || echo NO_REQUIREMENTS"),
        ("systemd services", "systemctl list-unit-files | grep lobster 2>/dev/null || echo NO_SYSTEMD_SERVICES"),
        ("Port 8000/8001 in use", "sudo fuser 8000/tcp 2>/dev/null; echo '---'; sudo fuser 8001/tcp 2>/dev/null; echo DONE"),
    ]

    for label, cmd in checks:
        print(f"\n=== {label} ===")
        out, err, code = run_cmd(client, cmd)
        if out.strip():
            print(out.strip())
        if err.strip():
            print(f"  [stderr] {err.strip()}")

    client.close()

if __name__ == "__main__":
    main()
