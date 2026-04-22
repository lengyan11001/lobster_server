"""Run remotely to extract system prompt from the latest OpenClaw log entry."""
import json, sys
with open("/opt/lobster-server/logs/app.log") as f:
    lines = f.readlines()

for line in reversed(lines):
    if "18:39" in line and "sutui-exchange" in line and "发往对方请求体=" in line:
        idx = line.find("发往对方请求体=")
        if idx < 0:
            continue
        json_str = line[idx + 8:]
        try:
            body = json.loads(json_str)
        except json.JSONDecodeError:
            continue
        msgs = body.get("messages", [])
        for m in msgs:
            if m.get("role") == "system":
                c = m.get("content", "")
                print("SYSTEM_LEN:", len(c))
                print("FIRST_500:", c[:500])
                print("---")
                for kw in ["flux", "jimeng", "image.generate", "fal-ai"]:
                    print(f"  CONTAINS '{kw}':", kw in c.lower())
                sys.exit(0)
        print("No system message found in messages array (has", len(msgs), "messages)")
        sys.exit(0)
print("No matching log line found")
