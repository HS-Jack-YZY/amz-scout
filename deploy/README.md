# amz-scout Webapp 部署手册（Phase 6）

amz-scout 内部 Webapp 在 AWS Lightsail 上的端到端「开通 → 部署 → 运维」
完整流程。目标：任何拥有 SSH 权限的同事，照着这份手册应该能在 **一小时内**
从零（重新）部署整套环境，全程不需要找 Jack。

> 文档中所有 `<占位符>` 请替换成你的真实值。
> 除占位符外，所有命令都按原样复制粘贴执行。

---

## 1. 前置条件

开始之前，准备好以下输入：

- **AWS Lightsail 账号**（已登录，已绑定账单）
- **你能控制的域名**（例如 `amz-scout.<gl-inet-internal-host>`），并且
  能在 DNS 服务商那里创建 A 记录
- **`KEEPA_API_KEY`** — Pro 套餐，60 token（Jack 的 `.env` 里已有）
- **`ANTHROPIC_API_KEY`** — 从 console.anthropic.com 拿到的 `sk-ant-...`
- **`CHAINLIT_AUTH_SECRET`** — 用 `chainlit create-secret` **生成一次**，
  之后**永远不要轮换**（轮换会强制踢掉所有在线会话）
- **`APP_PASSWORD`** — 强共享密码（每季度轮换一次）
- **`DEPLOY_EMAIL`** — `ops@gl-inet.com` 或类似邮箱，用于接收
  Let's Encrypt 的续签提醒
- 一对可加到 Lightsail 实例的 SSH 密钥

---

## 2. 开通 Lightsail 实例

在 Lightsail 控制台：

1. Create instance → **Linux/Unix** → **OS Only** → **Ubuntu 24.04 LTS**
2. 套餐：**\$12/月 — 2 GB 内存 / 2 vCPU / 60 GB SSD**（us-east-1
   或你偏好的区域）
3. 名称：`amz-scout-prod`（Task 12 演练用 `amz-scout-rehearsal-YYYYMMDD`）
4. 等到状态显示 **Running**

### 2a. 绑定静态公网 IP

Lightsail 默认的公网 IP 每次重启都会变 — 在指 DNS **之前** 先绑一个
静态 IP。

1. Lightsail 控制台 → **Networking** → **Create static IP**
2. 区域：与实例相同
3. 绑定到：`amz-scout-prod`
4. 记下 IP 地址 — 步骤 2c 会用到

### 2b. 在 Lightsail 防火墙开放 HTTP/HTTPS

**关键步骤 — 不要跳过。** Ubuntu 镜像默认只开 22 端口。如果不开 80/443，
Caddy 会一直卡在 ACME HTTP-01 挑战上拿不到证书。

1. Lightsail 控制台 → 实例 → **Networking** → **IPv4 Firewall**
2. 添加规则：**HTTP — TCP — 端口 80 — Source: Any IPv4**
3. 添加规则：**HTTPS — TCP — 端口 443 — Source: Any IPv4**
4. 确认两条规则都显示 **Enabled**

### 2c. 把 DNS 指向静态 IP

在 DNS 服务商那里创建：

```
amz-scout.<gl-inet-internal-host>.   IN  A   <STATIC_IP_FROM_2A>
TTL: 300（5 分钟 — 让切换成本更低）
```

从你的笔记本上验证 DNS 已生效：

```bash
dig +short amz-scout.<gl-inet-internal-host>
# 期望: <STATIC_IP_FROM_2A>
```

如果什么都没返回，等 1-5 分钟再试。这一步不通就不要继续往下做。

---

## 3. 挂载 20 GB 块存储磁盘

Webapp 的 SQLite 数据库就放在这块磁盘上，这样实例升级或重建时数据能保留。

1. Lightsail 控制台 → **Storage** → **Create disk**
2. 区域：与实例相同，**同一个可用区**
3. 大小：**20 GB**（约 \$2/月）
4. 名称：`amz-scout-data`
5. 绑定到：`amz-scout-prod`
6. 记下 Lightsail 分配的设备路径 — 通常是 `/dev/xvdf`

### 3a. 启用每日自动快照

1. Lightsail 控制台 → 实例 → **Snapshots** → **Enable automatic snapshots**
2. Lightsail 控制台 → 磁盘 → **Snapshots** → **Enable automatic snapshots**

实例和磁盘各自需要独立的快照策略。每日保留就足以覆盖我们的 RTO 预算。

---

## 4. 主机初始化

SSH 进入实例：

```bash
ssh ubuntu@<STATIC_IP_FROM_2A>
```

克隆代码仓库并运行幂等的初始化脚本：

```bash
cd ~
git clone https://github.com/<gl-inet-internal-org>/amz-scout.git
cd amz-scout
sudo BLOCK_DEVICE=/dev/xvdf bash deploy/first-time-setup.sh
```

脚本会做这些事：

- 安装 Docker Engine 和 Compose 插件
- 把 `ubuntu` 用户加进 `docker` 组
- 格式化 `/dev/xvdf`（**只在磁盘空白时**！）并挂载到 `/mnt/amz-scout-data`
- 在 `/etc/fstab` 写入一条带 `nofail` 的条目
- 创建 `/mnt/amz-scout-data/output` 目录并设好宽松权限

重新登录让 `ubuntu` 用户拿到 docker 组权限：

```bash
exit
ssh ubuntu@<STATIC_IP_FROM_2A>
docker version  # 不需要 sudo
```

把仓库本地的 `output/` 目录替换成指向挂载磁盘的软链接，让 SQLite 写入
落到持久卷上：

```bash
cd ~/amz-scout
rm -rf output
ln -s /mnt/amz-scout-data/output output
ls -ld output  # 应该显示软链接指向
```

---

## 5. 安装密钥

从你的笔记本（**不是** Lightsail 主机）：

```bash
scp ~/path/to/local/.env ubuntu@<STATIC_IP_FROM_2A>:~/amz-scout/.env
```

回到 Lightsail 主机：

```bash
cd ~/amz-scout
chmod 600 .env

# 追加部署相关的环境变量（或者编辑已有行）
cat >> .env <<'EOF'
DOMAIN=amz-scout.<gl-inet-internal-host>
DEPLOY_EMAIL=ops@gl-inet.com
EOF

# 确认必需变量都齐了
grep -E '^(KEEPA_API_KEY|ANTHROPIC_API_KEY|CHAINLIT_AUTH_SECRET|APP_PASSWORD|ALLOWED_EMAIL_DOMAIN|DOMAIN|DEPLOY_EMAIL)=' .env | wc -l
# 期望: 7
```

> **首次启动后永远不要轮换 `CHAINLIT_AUTH_SECRET`。** 它用来签 Session JWT，
> 一旦轮换就会把所有用户踢下线。把它当成数据库 schema：设一次，不要动。

---

## 6. 首次部署

```bash
cd ~/amz-scout
docker compose up -d --build
```

首次构建大约 8 分钟（Playwright Chromium 下载占大头）。一边构建一边
看日志：

```bash
docker compose logs -f webapp
# 看到这行表示 webapp 已起来:
#   "Webapp starting: model=claude-sonnet-4-6 db=/app/output/amz_scout.db"
# 按 Ctrl-C 停止 tail — 容器仍在后台运行

docker compose logs -f caddy
# 看到这行表示证书已签发（首次启动后约 30 秒）:
#   "certificate obtained successfully"
# 按 Ctrl-C 停止 tail
```

---

## 7. 冒烟测试

从笔记本（或任何装了 curl + python + 仓库的机器）：

```bash
scripts/smoke_deploy.sh https://amz-scout.<gl-inet-internal-host>
```

三项检查（HTTP 200、body 包含 "Chainlit"、pytest 集成冒烟）必须全通过。
任何一步失败请看下方排错章节。

接下来手动验证黄金路径：

1. 在无痕浏览器打开 URL
2. 用任意 `@gl-inet.com` 邮箱 + `.env` 里的 `APP_PASSWORD` 登录
3. 输入 "show me latest UK data"，确认工具调用返回 envelope
   （或者数据库为空时返回 freshness 提示）
4. 输入 "GL-Slate 7 在英国过去 7 天价格" — `query_trends` 应该自动
   抓 Keepa 数据（约 1 token）并渲染结果

---

## 8. 后续更新

```bash
ssh ubuntu@<STATIC_IP_FROM_2A>
cd ~/amz-scout
git pull
docker compose up -d --build
docker compose logs -f webapp  # 确认干净启动后按 Ctrl-C
```

然后从笔记本跑冒烟测试：

```bash
scripts/smoke_deploy.sh https://amz-scout.<gl-inet-internal-host>
```

---

## 9. 备份与恢复

### 备份

每日 Lightsail 快照同时覆盖实例和块存储磁盘（在步骤 3a 配置过）。MVP
阶段不需要额外的备份工具。

### 恢复（实例丢失）

1. Lightsail 控制台 → snapshots → 实例快照 → **Create instance from snapshot**
2. 选最新的快照
3. 重新绑定静态 IP（步骤 2a）
4. 重新挂载块存储磁盘（步骤 3）— 数据完好

### 恢复（数据损坏）

1. Lightsail 控制台 → snapshots → 磁盘快照 → **Create disk from snapshot**
2. SSH 进入实例，`sudo umount /mnt/amz-scout-data`
3. 卸下损坏的磁盘，挂上新磁盘（同样 `/dev/xvdf`）
4. `sudo mount -a`
5. `docker compose restart webapp`

---

## 10. 回滚

回滚到上一个 tag（或 commit）且不丢数据：

```bash
ssh ubuntu@<STATIC_IP_FROM_2A>
cd ~/amz-scout
git fetch --tags
git checkout <previous-tag-or-sha>
docker compose up -d --build
```

---

## 11. 排错

### "browser-use missing Chromium" 或 "chromium failed to launch"

原因：Dockerfile 层顺序坏了（browser-use 钉死的 Playwright 版本与已装
Chromium 不匹配）。**改 Dockerfile 重新构建**，**不要在主机上打补丁**。

```bash
docker compose build --no-cache webapp
docker compose up -d
```

### 登录返回 401

原因：`APP_PASSWORD` 或 `ALLOWED_EMAIL_DOMAIN` 写错。两个都查一下：

```bash
grep -E '^(APP_PASSWORD|ALLOWED_EMAIL_DOMAIN)=' .env
```

### TLS 失败 / Caddy 卡在 ACME 挑战

可能原因：

- Lightsail 防火墙的 80/443 还没开 → 重做 **步骤 2b**
- DNS 还没生效 → `dig +short $DOMAIN` 返回空或者错误的 IP
- 触发了 Let's Encrypt 速率限制（同域名 5 张证书/周）→ 等一周或者
  改用 ACME staging endpoint

```bash
docker compose logs caddy | tail -50
```

### 每次部署后所有用户都被强制踢下线

原因：`CHAINLIT_AUTH_SECRET` 被重新生成了。**永远不要轮换它。** 它用来
签 Session JWT，轮换就等于让所有在线 session 失效。从密码管理器里把
原值还原回来再重新部署。

### 写入数据库失败 / readonly database

```
sqlite3.OperationalError: attempt to write a readonly database
```

原因：`output/` 主机目录权限不对。容器以 root 身份运行，所以主机端
的 bind-mount 目录必须 root 可写。修复：

```bash
sudo chown -R root:root /mnt/amz-scout-data/output
sudo chmod 755 /mnt/amz-scout-data/output
docker compose restart webapp
```

### Caddy 返回 502 Bad Gateway

原因：webapp 容器崩溃了。先看日志：

```bash
docker compose logs webapp | tail -100
docker compose ps
```

重启：

```bash
docker compose restart webapp
```

### Webapp 内存占用超过 1.5 GB

Lightsail 2 GB 套餐可能不够用。升到 4 GB 套餐（约 \$20/月）。Lightsail
支持从快照在线变更套餐。

### 验证 secret 没有泄露到日志里

```bash
docker compose logs webapp | grep -c 'sk-ant-' || true
# 期望: 0
```

---

## 演练记录（Task 12）

把每次真实 Lightsail 演练的事故笔记按日期追加在这里，给后续的运维
留下「实际发生过什么」的真实记录，而不只是「计划应该发生什么」。

```
YYYY-MM-DD <名字>: <发生了什么 / 怎么修的 / 花了多久>
```

---

## 成本估算（MVP）

| 资源                         | 月度成本 |
|------------------------------|---------|
| Lightsail 2GB 实例           | \$12    |
| 静态公网 IP                  | 免费    |
| 20 GB 块存储                 | \$2     |
| 实例每日快照                 | \$0.05/GB |
| 磁盘每日快照                 | \$0.05/GB |
| **合计估算**                 | **~\$15-18** |

Anthropic + Keepa 的 API 用量另算，走各自的现有账号。
