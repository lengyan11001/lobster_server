"""Fix nginx to plain HTTP and update .env on domestic server."""
import sys
import os
import paramiko

SERVERS = {
    "domestic": {"host": "42.194.209.150", "user": "ubuntu", "password": "|EP^q4r5-)f2k"},
    "overseas": {"host": "43.162.111.36", "user": "ubuntu", "password": "BVK7XFn/;4P.bvG"},
}


def run_remote(srv_name, cmd, timeout=60):
    srv = SERVERS[srv_name]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(srv["host"], username=srv["user"], password=srv["password"],
                   timeout=15, look_for_keys=False, allow_agent=False)
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=True)
    out = stdout.read().decode(errors="replace")
    rc = stdout.channel.recv_exit_status()
    client.close()
    return rc, out


def write_remote(srv_name, path, content):
    srv = SERVERS[srv_name]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(srv["host"], username=srv["user"], password=srv["password"],
                   timeout=15, look_for_keys=False, allow_agent=False)
    sftp = client.open_sftp()
    with sftp.open(path, "w") as f:
        f.write(content)
    sftp.close()
    client.close()


def sudo_cmd(srv_name, cmd, timeout=30):
    pwd = SERVERS[srv_name]["password"]
    return run_remote(srv_name, f"echo '{pwd}' | sudo -S bash -c '{cmd}' 2>&1", timeout=timeout)


NGINX_PLAIN_HTTP = """server {
    listen 80;
    server_name _;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
"""

# 1. Fix domestic nginx: remove SSL, plain HTTP only
print("=== 1. Fix domestic nginx (remove SSL) ===")
write_remote("domestic", "/tmp/lobster.conf", NGINX_PLAIN_HTTP)
rc, out = sudo_cmd("domestic",
    "cp /tmp/lobster.conf /etc/nginx/sites-available/lobster && "
    "nginx -t && systemctl restart nginx"
)
safe = out.encode("ascii", "replace").decode("ascii")
print(f"  nginx: {safe.strip()[-300:]}")

# 2. Update .env on domestic
print("\n=== 2. Update domestic .env ===")
rc, out = run_remote("domestic", "cat /opt/lobster-server/.env")
old_env = out
if "PUBLIC_BASE_URL=" in old_env:
    new_env = []
    for line in old_env.split("\n"):
        if line.startswith("PUBLIC_BASE_URL="):
            new_env.append("PUBLIC_BASE_URL=http://42.194.209.150")
        else:
            new_env.append(line)
    write_remote("domestic", "/opt/lobster-server/.env", "\n".join(new_env))
    print("  Updated PUBLIC_BASE_URL")
else:
    # Append it
    rc2, _ = run_remote("domestic", 'echo "PUBLIC_BASE_URL=http://42.194.209.150" >> /opt/lobster-server/.env')
    print("  Appended PUBLIC_BASE_URL")

# 3. Verify domestic .env
rc, out = run_remote("domestic", "grep PUBLIC_BASE_URL /opt/lobster-server/.env")
print(f"  Verify: {out.strip()}")

# 4. Update .env on overseas (if needed)
print("\n=== 3. Check overseas .env ===")
rc, out = run_remote("overseas", "grep PUBLIC_BASE_URL /opt/lobster-server/.env || echo NOT_SET")
print(f"  Current: {out.strip()}")

print("\nDone!")
