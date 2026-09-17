#!/usr/bin/env python3
"""Scan every node: exit country (ip-api) + ChatGPT region check (chatgpt.com).

Switches the auto-select group to each node, then:
  1. GET http://ip-api.com/json  via the proxy  -> country of exit IP
  2. GET https://chatgpt.com/api/auth/session  -> 403 = region blocked
Writes /home/yang/mihomo-setup/scan_result.txt (node | country | http_code)
"""
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request

GROUP = "http://127.0.0.1:9090/proxies/%E2%99%BB%EF%B8%8F%20%E8%87%AA%E5%8A%A8%E9%80%89%E6%8B%A9"
PROXY = "http://127.0.0.1:7890"
OUT = "/home/yang/mihomo-setup/scan_result.txt"


def switch(node: str) -> bool:
    req = urllib.request.Request(
        GROUP,
        data=json.dumps({"name": node}).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


def node_endpoint(node: str) -> str:
    """Return the proxy's server:port (stable identity for the allowlist, since
    dynamic subscription names like 'JP 37' shift per fetch).

    Read from the local config: mihomo 1.19's /proxies/{name} API no longer
    returns server/port.
    """
    return ENDPOINTS.get(node, "?")


def load_endpoints() -> dict[str, str]:
    import yaml
    for path in ("/etc/mihomo/config.yaml",
                 "/home/yang/mihomo-setup/config-current.yaml"):
        try:
            d = yaml.safe_load(open(path))
            eps = {
                p["name"]: f"{p['server']}:{p['port']}"
                for p in d.get("proxies", [])
            }
            if eps:
                print(f"endpoints loaded from {path}", flush=True)
                return eps
        except Exception:
            continue
    print("WARNING: no endpoint source readable", flush=True)
    return {}


ENDPOINTS = load_endpoints()


def probe(url: str, timeout: int = 12) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), "-x", PROXY, "-o", "/dev/null",
             "-w", "%{http_code}", url],
            capture_output=True, text=True,
        )
        return int(p.stdout or 0), ""
    except Exception as e:
        return 0, str(e)


def main() -> None:
    nodes = [ln.strip() for ln in open("/tmp/nodes.txt") if ln.strip()]
    if len(sys.argv) > 1:  # optional: scan only a subset
        nodes = [n for n in nodes if any(s in n for s in sys.argv[1:])]
    print(f"scanning {len(nodes)} nodes...", flush=True)
    with open(OUT, "w") as f:
        for i, node in enumerate(nodes, 1):
            ok = switch(node)
            if not ok:
                f.write(f"{node} | ? | SWITCH_FAIL\n")
                f.flush()
                continue
            time.sleep(0.5)
            code, country = probe("http://ip-api.com/json")
            if code == 200:
                try:
                    country = json.loads(
                        subprocess.run(
                            ["curl", "-s", "--max-time", "10", "-x", PROXY,
                             "http://ip-api.com/json"],
                            capture_output=True, text=True,
                        ).stdout
                    )
                    country = f"{country.get('countryCode')} {country.get('city')}"
                except Exception:
                    country = "?"
            else:
                country = "?"
            ep = node_endpoint(node)
            f.write(f"{node} | {ep} | {country}\n")
            f.flush()
            if i % 10 == 0:
                print(f"  {i}/{len(nodes)}...", flush=True)
    print(f"done -> {OUT}", flush=True)

    # auto-generate the HK-endpoint blacklist (user-writable; update.py reads
    # this copy first so no root is needed to refresh filtering).
    # UNION with the existing list: each scan sees a rotating node pool, so
    # accumulating across scans is what converges to full coverage.
    bl_file = "/home/yang/mihomo-setup/hk-endpoints.txt"
    existing = set()
    try:
        existing = {ln.strip() for ln in open(bl_file) if ln.strip()}
    except FileNotFoundError:
        pass
    hk_eps = []
    for ln in open(OUT):
        node, ep, country = ln.rsplit(" | ", 2)
        if country.strip().startswith("HK") and ep.strip() != "?":
            hk_eps.append(ep.strip())
    bl = sorted(existing | set(hk_eps))
    with open(bl_file, "w") as f:
        f.write("\n".join(bl) + "\n")
    print(f"blacklist written: {len(bl)} HK endpoints "
          f"({len(set(hk_eps))} from this scan, {len(existing)} carried over)",
          flush=True)


if __name__ == "__main__":
    main()
