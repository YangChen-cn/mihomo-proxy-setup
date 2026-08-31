# Mihomo 本地代理一键部署（Linux VM）

在 Linux 虚拟机上用 **mihomo (Clash Meta) + systemd** 搭一个本地 7890 代理：
开机自启、命令行一键开关、订阅自动导入并**剔除香港出口节点**（OpenAI/GPT/Codex 可用）。

不开 TUN、不碰系统路由表、不动 Tailscale 规则。

## 功能

- **本地混合端口 `127.0.0.1:7890`**（HTTP + SOCKS5），不监听局域网
- **systemd 开机自启**：`mihomo.service`，崩溃自动重启
- **一键开关**：`proxy on | off | status | restart | update`
  - `on` 启动服务并写入 shell 代理环境变量（当前 shell + 新 shell 都生效）
  - `update` 重新拉取订阅、套用过滤、重建配置并重启，`store-selected` 保留你手动选的节点
- **订阅自动导入**：以 `clash.meta` UA 请求，服务端直接返回完整 Clash YAML（288+ 节点），无需手动转换
- **自动剔除香港出口节点**：双保险过滤（见下文），保证 OpenAI/ChatGPT/Codex 地区检查能过
- **OpenAI 域名固定路由**：`chatgpt.com` / `openai.com` / `oaistatic.com` / `oaiusercontent.com` 走你选择的节点组，其余按规则分流（国内直连）
- **不动 Tailscale**：规则里显式 `100.64.0.0/10 → DIRECT`，Tailscale 流量永不进代理

## 目录结构

```
├── README.md          # 本文档
├── install.sh         # 一键安装脚本（需要 root，SUB_URL 环境变量传入订阅地址）
├── mihomo.service     # systemd 单元
├── proxy              # 一键开关脚本（proxy on/off/status/restart/update）
├── proxy-nopasswd     # sudoers 免密模板（把 yang 替换成你的用户名）
├── update.py          # 订阅抓取器 + 配置生成器（含 HK 过滤）
├── scan_nodes.py      # 节点扫描器：逐个测出口国家，生成 hk-endpoints.txt
├── hk-endpoints.txt   # 已验证为香港出口的节点 IP 黑名单（扫描产物，示例数据）
└── .gitignore
```

## 快速开始

```bash
# 1. 准备：下载 mihomo 二进制（x86_64 示例，其他架构到 releases 页找）
curl -sL -o /tmp/mihomo.gz "https://github.com/MetaCubeX/mihomo/releases/download/v1.19.30/mihomo-linux-amd64-v1-v1.19.30.gz"
gunzip -f /tmp/mihomo.gz && chmod +x /tmp/mihomo

# 2. 安装（脚本会把二进制装到 /usr/local/bin，配置生成到 /etc/mihomo）
SUB_URL="https://your-provider.example/sub?token=xxxx" sudo bash install.sh

# 3. 开启代理
proxy on        # 建议新开一个 shell（或 source ~/.bashrc 后使用 proxy 函数）

# 4. 验证
curl -x http://127.0.0.1:7890 http://ip-api.com/json   # 看出口 IP 和地区
codex            # Rust 版 codex 原生读取 http_proxy/https_proxy，无需额外配置
```

> `proxy` 是 `/usr/local/bin/proxy` 脚本 + `~/.bashrc` 里的同名函数。
> 函数让 `on/off` 能**即时**改当前 shell 的环境变量；脚本本体用 sudo 执行
> （`sudoers.d` 免密规则，见下）。

### sudoers 免密（可选但推荐）

```bash
sudo install -m 440 proxy-nopasswd /etc/sudoers.d/proxy-nopasswd
# 注意先把文件里的 "yang" 改成你的用户名
```

## HK 节点过滤机制（重点）

这是本项目的核心坑，也是价值所在：

**问题**：免费订阅的节点是 Cloudflare 中转，节点名与国家无关。实测 196 个节点中，
名字写 `JP / KR / SG / TW` 的节点，有 70 个出口实际是**香港**（Cloudflare HK 边缘）。
OpenAI 按出口 IP 判定地区：**香港边缘必 403 `unsupported_country_region_territory`**，
日本/台湾/新加坡边缘放行。`curl https://chatgpt.com/...` 拿到的 403 是 Cloudflare
bot 风控页（HTML），**不能**当地区判定探针。

**过滤 = 名字模式 + 端点黑名单双保险**（`update.py`）：

1. **稳定名字模式**（100% 香港出口，订阅器命名规律固定）：
   `官方优选`、`TG:@MiaChatChannel`、`高速 by Jz`、`优选高速`、`MO` 前缀
2. **`hk-endpoints.txt` 端点黑名单**：扫描验证过的香港出口 IP 列表，
   兜底名字伪装节点（如实测 `TW 7` / `TW 10` 出口是香港）
3. 原有 `HK / HKG / 香港` 名字过滤保留

**为什么不用精确白名单**：这个订阅每次抓取节点池都在轮换（序号、IP 都变），
白名单必然大面积失效；名字模式和 IP 黑名单不受轮换影响。

### 重新扫描节点（订阅大洗牌时）

```bash
# 1. 恢复全量节点配置（临时移除黑名单）
sudo mv /etc/mihomo/hk-endpoints.txt /tmp/
sudo bash -c 'cd /etc/mihomo && python3 update.py && systemctl restart mihomo'

# 2. 从 API 导出节点名单，逐个测出口国家（约 5 分钟）
curl -s http://127.0.0.1:9090/proxies | python3 -c \
  "import json,sys; d=json.load(sys.stdin)['proxies']; print('\n'.join(k for k,v in d.items() if v.get('type')=='Vless'))" > /tmp/nodes.txt
python3 scan_nodes.py            # 输出 scan_result.txt（节点 | server:port | 出口国家）

# 3. 重建黑名单（丢弃 HK 出口的），恢复并更新
python3 - <<'EOF'
eps = []
for ln in open('scan_result.txt'):
    node, ep, country = ln.rsplit(' | ', 2)
    if country.strip().startswith('HK') and ep != '?':
        eps.append(ep)
open('hk-endpoints.txt', 'w').write('\n'.join(sorted(set(eps))) + '\n')
print(len(set(eps)), "HK endpoints")
EOF
sudo install -m 600 -o root -g root hk-endpoints.txt /etc/mihomo/
sudo bash -c 'cd /etc/mihomo && python3 update.py && systemctl restart mihomo'
```

## 使用 Codex / GPT

Rust 原生版 Codex CLI 直接读取 `http_proxy` / `https_proxy` 环境变量 ——
**不需要** `~/.codex/.env`，也**不需要**把 WebSocket 改成 HTTP。

```bash
proxy on          # 环境变量就位（新 shell）
codex             # 直接用
```

`proxy off` 后 codex 会走直连，OpenAI 地区检查会拒绝（预期行为）。

## 配置生成器行为说明（update.py）

- 订阅响应：优先解析 Clash YAML（`clash.meta` UA），失败回退 base64 vless 列表
- 抓取：pages.dev 部分 Cloudflare 边缘 IP 在本网络路径被黑洞（TCP/TLS 通但响应不回），
  已内置"解析全部 A 记录、逐个 IP 重试"
- 生成：`mixed-port: 7890`、`allow-lan: false`、`mode: rule`、
  `external-controller: 127.0.0.1:9090`、`store-selected: true`、无 TUN
- 规则：OpenAI 域名 → 节点选择组；Tailscale/CGNAT/内网 → DIRECT；GEOIP CN → DIRECT

## 常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| `proxy on` 要输密码 | sudoers 免密未配置 | 按上文装 `proxy-nopasswd` |
| codex 403 `unsupported_country` | 出口被判定香港 | `proxy update` 重建（新节点池）或 `scan_nodes.py` 重扫 |
| `proxy update` 后节点变少 | 订阅池轮换，新 IP 不在黑名单内即保留，名字高风险类别被滤 | 正常；大洗牌时重扫 |
| 网页 curl 403 | Cloudflare bot 风控页，非地区问题 | 用真实浏览器/codex 验证 |

## License

MIT
