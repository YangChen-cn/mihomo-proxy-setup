#!/usr/bin/env python3
"""Probe every node against the ChatGPT region check via mihomo's own
delay-testing API (same HTTP stack the proxy uses, so no Cloudflare bot
block). A node that fails here cannot carry Codex traffic right now.

Usage: probe_openai.py [delay|gstatic]   (default: chatgpt check)
Output: probe_result.txt  (node | delay_ms_or_ERROR | detail)
"""
import concurrent.futures
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "http://127.0.0.1:9090"
TARGETS = {
    "chatgpt": "https://chatgpt.com/api/auth/session",
    "gstatic": "http://www.gstatic.com/generate_204",
}
OUT = "/home/yang/mihomo-setup/probe_result.txt"


def node_names() -> list[str]:
    d = json.loads(urllib.request.urlopen(f"{API}/proxies", timeout=10).read())
    return [k for k, v in d["proxies"].items() if v.get("type") == "Vless"]


def probe(node: str, url: str, timeout_ms: int = 8000):
    q = f"{API}/proxies/{urllib.parse.quote(node, safe='')}/delay"
    q += f"?url={urllib.parse.quote(url, safe='')}&timeout={timeout_ms}"
    try:
        d = json.loads(urllib.request.urlopen(q, timeout=timeout_ms / 1000 + 8).read())
        return node, d.get("delay"), ""
    except urllib.error.HTTPError as e:
        return node, None, e.read().decode(errors="replace")[:120]
    except Exception as e:
        return node, None, str(e)[:120]


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "chatgpt"
    url = TARGETS[which]
    nodes = node_names()
    print(f"probing {len(nodes)} nodes against {which} ...", flush=True)
    ok, bad = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        for node, delay, err in ex.map(lambda n: probe(n, url), nodes):
            if delay is not None:
                ok.append((node, delay))
            else:
                bad.append((node, err))
    with open(OUT, "w") as f:
        for n, d in sorted(ok, key=lambda x: x[1]):
            f.write(f"{n} | {d} | OK\n")
        for n, e in bad:
            f.write(f"{n} | ERR | {e}\n")
    print(f"OK: {len(ok)}   FAIL: {len(bad)}   -> {OUT}")
    for n, e in bad[:8]:
        print(f"  FAIL {n}: {e}")


if __name__ == "__main__":
    main()
