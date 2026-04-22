#!/usr/bin/env python3
"""Setup test server: check state, add SSH key, clone repo if needed."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
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
    print(f"[1] Connecting to {USER}@{HOST} with password ...")
    client.connect(HOST, username=USER, password=PASSWORD, timeout=15)
    print("[1] Connected!")

    print("\n[2] Checking server info ...")
    out, _, _ = run_cmd(client, "hostname; uname -a; cat /etc/os-release | head -5")
    print(out)

    print("[3] Checking /opt/lobster-server ...")
    out, _, code = run_cmd(client, "ls -la /opt/lobster-server/.git 2>/dev/null && echo REPO_EXISTS || echo NO_REPO")
    print(out)

    print("[4] Checking if SSH public key is already authorized ...")
    pubkey_path = "D:/maczhuji.pub"
    if os.path.exists(pubkey_path):
        with open(pubkey_path, "r") as f:
            pubkey = f.read().strip()
        out, _, _ = run_cmd(client, "cat ~/.ssh/authorized_keys 2>/dev/null || echo NO_AUTH_KEYS")
        if pubkey.split()[1] in out:
            print("  -> Public key already in authorized_keys")
        else:
            print("  -> Public key NOT found, adding ...")
            cmd = f'mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo "{pubkey}" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
            _, err, code = run_cmd(client, cmd)
            if code == 0:
                print("  -> SSH public key added successfully!")
            else:
                print(f"  -> Failed to add key: {err}")
    else:
        print(f"  -> No public key file found at {pubkey_path}, checking other locations ...")
        for p in ["D:/maczhuji.pub", os.path.expanduser("~/.ssh/id_rsa.pub"), os.path.expanduser("~/.ssh/id_ed25519.pub")]:
            if os.path.exists(p):
                print(f"  -> Found: {p}")
                break
        else:
            print("  -> No public key found. Will extract from private key.")
            out_key, _, _ = run_cmd(client, "echo placeholder")
            import subprocess
            result = subprocess.run(
                ["C:\\Program Files\\Git\\usr\\bin\\ssh-keygen.exe", "-y", "-f", "D:/maczhuji", "-P", "lengyan2"],
                capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                pubkey = result.stdout.strip()
                print(f"  -> Extracted public key: {pubkey[:60]}...")
                out, _, _ = run_cmd(client, "cat ~/.ssh/authorized_keys 2>/dev/null || echo NO_AUTH_KEYS")
                if pubkey.split()[1] in out:
                    print("  -> Key already authorized")
                else:
                    cmd = f'mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo "{pubkey}" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
                    _, err, code = run_cmd(client, cmd)
                    if code == 0:
                        print("  -> SSH public key added!")
                    else:
                        print(f"  -> Failed: {err}")
            else:
                print(f"  -> Cannot extract public key: {result.stderr}")

    print("\n[5] Checking git, python3 on remote ...")
    out, _, _ = run_cmd(client, "which git; git --version; which python3; python3 --version")
    print(out)

    print("[6] Checking disk space ...")
    out, _, _ = run_cmd(client, "df -h /opt")
    print(out)

    client.close()
    print("\n[DONE]")

if __name__ == "__main__":
    main()
