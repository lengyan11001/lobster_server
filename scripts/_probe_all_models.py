"""Probe all SuTui models: get list, check docs pricing, and check llms.txt availability."""
import httpx
import json
import time
from urllib.parse import quote

BASE = "https://api.xskill.ai"
APIZ_BASE = "https://api.apiz.ai"

r = httpx.get(f"{BASE}/api/v3/models?lang=zh", timeout=15.0)
models_data = r.json().get("data", {}).get("models", [])
print(f"Total models from /api/v3/models: {len(models_data)}")

results = []
for m in models_data:
    mid = m.get("name", "")
    name = m.get("display_name") or m.get("name", "")
    tags = m.get("tags", [])

    safe = quote(mid, safe="")

    docs_status = "?"
    pricing_type = ""
    base_price = ""
    per_s = ""
    try:
        dr = httpx.get(f"{BASE}/api/v3/models/{safe}/docs?lang=zh", timeout=15.0)
        if dr.status_code == 404:
            docs_status = "NO_DOCS"
        else:
            dj = dr.json()
            if dj.get("code") == 200:
                pricing = (dj.get("data") or {}).get("pricing", {})
                if pricing:
                    docs_status = "HAS_PRICING"
                    pricing_type = pricing.get("price_type", "")
                    base_price = str(pricing.get("base_price", ""))
                    per_s = str(pricing.get("per_second", ""))
                else:
                    docs_status = "DOCS_NO_PRICING"
            else:
                docs_status = f"DOCS_ERR_{dj.get('code')}"
    except Exception as e:
        docs_status = f"EXCEPTION"

    llms_status = "?"
    try:
        lr = httpx.get(f"{APIZ_BASE}/api/v3/models/{safe}/llms.txt", timeout=10.0)
        if lr.status_code == 200 and len(lr.text) > 50:
            llms_status = "OK"
        elif lr.status_code == 404:
            llms_status = "NOT_FOUND"
        else:
            llms_status = f"HTTP_{lr.status_code}"
    except Exception:
        llms_status = "EXCEPTION"

    results.append({
        "model_id": mid,
        "docs": docs_status,
        "pricing_type": pricing_type,
        "base_price": base_price,
        "per_second": per_s,
        "llms_txt": llms_status,
    })

    time.sleep(0.1)

print(f"\n{'Model ID':<60} {'Docs':<16} {'Pricing':<20} {'Base':<8} {'per_s':<8} {'llms.txt'}")
print("-" * 130)
for r in sorted(results, key=lambda x: x["model_id"]):
    print(f"{r['model_id']:<60} {r['docs']:<16} {r['pricing_type']:<20} {r['base_price']:<8} {r['per_second']:<8} {r['llms_txt']}")

has_pricing = sum(1 for r in results if r["docs"] == "HAS_PRICING")
no_docs = sum(1 for r in results if r["docs"] == "NO_DOCS")
has_llms = sum(1 for r in results if r["llms_txt"] == "OK")
print(f"\nSummary: {has_pricing} with pricing, {no_docs} no docs, {has_llms} with llms.txt, {len(results)} total")

with open("scripts/_probe_all_models_result.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("\nResults saved to scripts/_probe_all_models_result.json")
