#!/usr/bin/env python3
"""Fetch subscription, convert to mihomo config, drop HK nodes.

The subscription server returns a full Clash YAML when requested with the
"clash.meta" user agent (preferred), or a base64 vless:// list otherwise.
Usage: update.py [sub-url] [output.yaml]
Reads sub-url from first arg, or from /etc/mihomo/sub-url, or $SUB_URL.
"""
import base64
import json
import os
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path

SUB_URL_FILE = Path("/etc/mihomo/sub-url")
OUTPUT_DEFAULT = Path("/etc/mihomo/config.yaml")

# HK filter: name mentions HK / HKG / 香港. Note: \b is useless here because
# python \w is Unicode-aware (CJK chars are word chars). Instead require the
# chars around HK/HKG to be non-ASCII-alnum (CJK, emoji, punctuation all fit).
HK_RE = re.compile(r"(?:^|[^A-Za-z0-9])(?:HK|HKG)(?:[^A-Za-z0-9]|$)|香港", re.I)


def fetch_subscription(url: str) -> bytes:
    """Fetch with curl, retrying across all resolved A records.

    On this VM only SOME of the subscription host's Cloudflare edge IPs are
    reachable (others accept TCP/TLS but blackhole the response), so a single
    DNS round-robin pick may hang. Try every IPv4 until one succeeds.
    """
    import socket
    import subprocess

    host = urllib.parse.urlparse(url).hostname
    try:
        addrs = sorted(
            {
                info[4][0]
                for info in socket.getaddrinfo(host, 443, socket.AF_INET)
            }
        )
    except Exception:
        addrs = []
    attempts = [([], "dns-round-robin")]
    attempts += [
        (["--resolve", f"{host}:443:{ip}", "--retry", "1"], f"ip {ip}")
        for ip in addrs
    ]
    for extra, label in attempts:
        proc = subprocess.run(
            ["curl", "-sL", "--max-time", "60", "-A", "clash.meta", "--retry", "2"]
            + extra
            + [url],
            capture_output=True,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
        print(f"  fetch attempt ({label}) failed rc={proc.returncode}", flush=True)
    raise SystemExit("subscription fetch failed after all attempts")


def load_proxies(raw: bytes) -> tuple[list[dict], str]:
    """Return (list of proxy dicts in mihomo form, source name)."""
    text = raw.decode("utf-8", errors="replace")
    # Prefer: server-side Clash YAML
    if re.search(r"^proxies:", text, re.M):
        try:
            import yaml
        except ImportError:
            yaml = None
        if yaml is not None:
            try:
                data = yaml.safe_load(text)
                if isinstance(data, dict) and data.get("proxies"):
                    return data["proxies"], "clash-yaml"
            except Exception as e:
                print(f"yaml parse failed, falling back: {e}")
    # Fallback: base64-encoded vless:// links
    stripped = text.strip().replace("\n", "").replace("\r", "")
    try:
        decoded = base64.b64decode(stripped).decode("utf-8", errors="replace")
        lines = [ln for ln in decoded.splitlines() if ln.strip()]
    except Exception:
        lines = []
    proxies = []
    for line in lines:
        if not line.startswith("vless://"):
            continue
        p = parse_vless(line)
        if p is not None:
            proxies.append(p)
    if not proxies:
        raise SystemExit("could not parse subscription (not YAML, no vless links)")
    return proxies, "base64-vless"
    # subscription payload is usually base64 (may be line-wrapped)
    stripped = raw.strip().replace("\n", "").replace("\r", "")
    try:
        decoded = base64.b64decode(stripped).decode("utf-8", errors="replace")
        lines = [ln for ln in decoded.splitlines() if ln.strip()]
    except Exception:
        lines = [ln for ln in raw.splitlines() if ln.strip()]
    if not lines:
        raise SystemExit("subscription fetched but empty")
    return lines


def parse_vless(uri: str) -> dict | None:
    """Parse a vless:// URI into a mihomo proxy dict."""
    try:
        parsed = urllib.parse.urlparse(uri)
        if parsed.scheme != "vless":
            return None
        userinfo, hostport = parsed.netloc.rsplit("@", 1)
        host, port = hostport.rsplit(":", 1)
        uuid = userinfo
        name = urllib.parse.unquote(parsed.fragment or uuid[:8])
        params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))

        security = params.get("security", "none")
        network = params.get("type", "tcp")
        host = params.get("host", host)

        proxy = {
            "name": name,
            "type": "vless",
            "server": host,
            "port": int(port),
            "uuid": uuid,
            "udp": True,
        }
        if security in ("tls", "reality"):
            proxy["tls"] = True
            sni = params.get("sni") or params.get("host") or host
            if sni and sni != host:
                proxy["servername"] = sni
            if params.get("fp"):
                proxy["client-fingerprint"] = params["fp"]
        if security == "reality":
            proxy["reality-opts"] = {
                "public-key": params.get("pbk", ""),
                "short-id": params.get("sid", ""),
            }
            if params.get("flow"):
                proxy["flow"] = params["flow"]
        if network == "ws":
            proxy["network"] = "ws"
            ws = {"path": params.get("path", "/")}
            if params.get("host"):
                ws["headers"] = {"Host": params["host"]}
            proxy["ws-opts"] = ws
        elif network == "grpc":
            proxy["network"] = "grpc"
            proxy["grpc-opts"] = {"grpc-service-name": params.get("serviceName", "")}
        elif network not in ("", "tcp"):
            proxy["network"] = network
            if params.get("path"):
                proxy.setdefault(network + "-opts", {})["path"] = params["path"]
        return proxy
    except Exception:
        return None


def generate_config(proxies: list[dict], output: Path) -> None:
    # dedupe: same server:port twice -> keep first; duplicate names -> suffix
    seen_ep, seen_name = set(), {}
    clean = []
    for p in proxies:
        key = (p["server"], p["port"])
        if key in seen_ep:
            continue
        seen_ep.add(key)
        name = p["name"]
        if name in seen_name:
            seen_name[name] += 1
            name = f"{name} #{seen_name[name]}"
            p = {**p, "name": name}
        else:
            seen_name[name] = 1
        clean.append(p)
    if not clean:
        raise SystemExit("no proxies left after filtering")

    names = [p["name"] for p in clean]
    yaml_names = ", ".join(json.dumps(n, ensure_ascii=False) for n in names)

    config = f"""\
mixed-port: 7890
allow-lan: false
mode: rule
log-level: info
external-controller: 127.0.0.1:9090
profile:
  store-selected: true

proxies:
{_dump_proxies(clean)}

proxy-groups:
  - name: "🚀 节点选择"
    type: select
    proxies: ["♻️ 自动选择", "DIRECT"]
  - name: "♻️ 自动选择"
    type: url-test
    url: "http://www.gstatic.com/generate_204"
    interval: 300
    tolerance: 50
    proxies: [{yaml_names}]

rules:
  - DOMAIN-SUFFIX,chatgpt.com,🚀 节点选择
  - DOMAIN-SUFFIX,openai.com,🚀 节点选择
  - DOMAIN-SUFFIX,oaistatic.com,🚀 节点选择
  - DOMAIN-SUFFIX,oaiusercontent.com,🚀 节点选择
  - IP-CIDR,100.64.0.0/10,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR6,fc00::/7,DIRECT,no-resolve
  - GEOIP,CN,DIRECT
  - MATCH,🚀 节点选择
"""
    tmp = tempfile.NamedTemporaryFile("w", dir=output.parent, delete=False, suffix=".tmp")
    try:
        tmp.write(config)
        tmp.close()
        os.replace(tmp.name, output)
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
    print(f"wrote {output}: {len(clean)} proxies (HK nodes removed)")


def _dump_proxies(proxies: list[dict]) -> str:
    return "".join(
        "  - " + "\n    ".join(_yaml_block(p).splitlines()) + "\n" for p in proxies
    )


def _yaml_block(proxy: dict, indent: int = 0) -> str:
    pad = "  " * indent
    lines = []
    for k, v in proxy.items():
        if isinstance(v, dict):
            lines.append(f"{pad}{k}:")
            lines.append(_yaml_block(v, indent + 1))
        elif isinstance(v, bool):
            lines.append(f"{pad}{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{pad}{k}: {v}")
        else:
            # ensure_ascii=False: go-yaml rejects \udXXX surrogate escapes
            lines.append(f"{pad}{k}: {json.dumps(v, ensure_ascii=False)}")
    return "\n".join(lines)


ALLOWLIST_FILE = Path("/etc/mihomo/codex-allowlist.txt")
HK_ENDPOINTS_FILE = Path("/etc/mihomo/hk-endpoints.txt")

# Name categories with 100% HK exit (verified by scanning 196 nodes):
# the subscription labels these nodes JP/KR/SG/... but every one of them
# exits via Cloudflare's Hong Kong edge. Pattern names are stable even
# though the node pool rotates between fetches.
DROP_NAME_RE = re.compile(
    r"官方优选|TG:@MiaChatChannel|高速 by Jz|优选高速|^MO(?: \d+)?$"
)


def load_hk_endpoints() -> set[str]:
    """Endpoints ("server:port") observed exiting from Hong Kong during scan.

    Backup for the name patterns above: catches nodes whose names don't
    reveal their HK exit (e.g. "TW 7"). Built by scan_nodes.py.
    """
    if not HK_ENDPOINTS_FILE.exists():
        return set()
    return {ln.strip() for ln in HK_ENDPOINTS_FILE.read_text().splitlines() if ln.strip()}


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SUB_URL", "")
    if not url and SUB_URL_FILE.exists():
        url = SUB_URL_FILE.read_text().strip()
    if not url:
        raise SystemExit("no subscription url (pass as argv, or write /etc/mihomo/sub-url)")
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else OUTPUT_DEFAULT
    output.parent.mkdir(parents=True, exist_ok=True)

    raw = fetch_subscription(url)
    proxies, source = load_proxies(raw)
    hk_eps = load_hk_endpoints()
    dropped = 0
    kept = []
    for p in proxies:
        name = str(p.get("name", ""))
        ep = f"{p['server']}:{p['port']}"
        if HK_RE.search(name) or DROP_NAME_RE.search(name) or ep in hk_eps:
            dropped += 1
            continue
        kept.append(p)
    leftover = [p["name"] for p in kept if HK_RE.search(str(p.get("name", "")))]
    if leftover:
        raise SystemExit(f"BUG: HK nodes still present after filter: {leftover[:5]}")
    print(f"source: {source}, total: {len(proxies)}, dropped: {dropped}, kept: {len(kept)}")
    generate_config(kept, output)


if __name__ == "__main__":
    main()
