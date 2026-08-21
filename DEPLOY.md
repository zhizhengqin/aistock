# 睿见投研 京东云部署手册（小白版 · 与 GS-Tracker 共存）

> **最终效果**：你在自己电脑上改代码 → `git push` 到 GitHub → GitHub 自动登录你的京东云服务器 → 自动拉代码、重建、重启。和 GS-Tracker 体验一样，push 完什么都不用管。
>
> **你的服务器**：Ubuntu 24.04，4 核 16G，公网 IP `111.228.23.109`，**已经跑着 GS-Tracker**（金桥智讯）。本手册保证不动它一根汗毛。
>
> **全程约 40~60 分钟**（大头是 Docker 首次构建），每一步都是复制粘贴命令。遇到问题先翻最后的「故障排查」。

---

## 两套系统怎么共存（先看懂这张表）

| | GS-Tracker（已有，不动） | 睿见投研（本次部署） |
|---|---|---|
| 代码目录 | `/root/gs-tracker` | `/opt/aistock` |
| 对外地址 | http://111.228.23.109 | **http://111.228.23.109:8080**（HTTPS 后：https://域名:8443） |
| 占用端口 | 80、443 | **8080、8443** |
| 容器名 | gs-tracker-app / scheduler / nginx | aistock-pg / redis / api / worker / nginx |
| compose 项目名 | deploy | **aistock**（已在配置里写死，互不干扰） |
| 数据目录 | /root/gs-tracker/data | /data/aistock |

**为什么是 8080/8443**：一台服务器的 80/443 端口同时只能给一个程序，已经被 GS-Tracker 的 nginx 占了。睿见投研用 8080（HTTP）和 8443（HTTPS），两边互不影响。

---

## 准备东西（先确认都有）

| 物品 | 说明 |
|---|---|
| 京东云控制台账号 | 能登录 jdcloud.com，第 3 步要改安全组 |
| 服务器 root 密码 | 和 GS-Tracker 是同一台机器、同一个密码 |
| 你本机的终端 | Mac 用「终端」App |
| DeepSeek API Key | https://platform.deepseek.com 申请，充 10 块够用很久；暂时没有也行，第 5 步教你先用 mock 模式跑通 |
| 一个域名（仅 HTTPS 需要） | 第 9 步才用，前面不需要。阿里云/腾讯云买，便宜的 .top/.cn 一年十几块 |

手册里代码块中的命令默认都是在**服务器上**执行（可直接整段复制粘贴）；标注 `本机$` 开头的是在你自己的 Mac 上执行。

---

## 第 1 步：登录服务器

打开 Mac 的「终端」，输入：

```bash
本机$ ssh root@111.228.23.109
```

- 输入 root 密码（**输入时屏幕什么都不显示，是正常的**，输完回车）
- 看到 `root@lavm-xxxxx:~#` 就说明进去了

**后面第 2~10 步的命令全都在服务器上执行。**

---

## 第 2 步：环境检查（Docker 和 Git 代理，部署 GS-Tracker 时已装好）

逐条复制执行，**只验证、不安装**：

```bash
docker --version && docker compose version
git config --global --get-regexp 'url\..*insteadof'
```

- 第一条要看到 `Docker version 26.x` 和 `Docker Compose version v2.x`
- 第二条要看到一行 `url.https://gh-proxy.com/... insteadof ...`（这是 GitHub 加速代理，部署 GS-Tracker 时配过，睿见投研拉代码也会自动走它）

哪条没有输出，就翻开 GS-Tracker 的 DEPLOY.md 重做对应的第 2 步 / 第 3.1 步（一次就好，两台系统共用）。

顺便确认 8080/8443 端口没被别的程序占用（正常应该**没有任何输出**）：

```bash
ss -tlnp | grep -E ':(8080|8443) '
```

有输出就把原文发给我。

---

## 第 3 步：京东云安全组放行 8080 和 8443（网页操作）

服务器本身之外，京东云还有一层网页上的防火墙（安全组），新端口必须在那里放行：

1. 浏览器登录京东云控制台 →「云主机」→ 找到这台实例 → 点实例名进详情
2. 点「安全组」标签 → 点安全组名称进详情 →「入站规则」→「添加规则」
3. 加两条规则：
   - 类型「自定义 TCP」，端口范围填 `8080`，源 `0.0.0.0/0`，备注「aistock-http」
   - 同样方法再加一条，端口 `8443`，备注「aistock-https」

> 80/443/22 部署 GS-Tracker 时已经放行过，不用动。

---

## 第 4 步：把代码拉到服务器

```bash
git clone https://github.com/zhizhengqin/aistock.git /opt/aistock
cd /opt/aistock && ls
```

看到 `README.md`、`backend/`、`frontend/`、`deploy/` 这些文件就对了。

> 如果 clone 报 `GnuTLS recv error` 或超时，说明 GitHub 代理没生效，重做第 2 步的代理检查。

---

## 第 5 步：创建配置文件 deploy/.env（最关键的一步）

`.env` 存放密码和密钥，**不会**上传到 GitHub，只在服务器上。

**5.1 先生成两个随机密码**（复制执行，会输出两行随机字符串，先记下来）：

```bash
openssl rand -hex 32
openssl rand -hex 16
```

**5.2 创建并编辑配置文件**：

```bash
cd /opt/aistock
cp deploy/.env.example deploy/.env
nano deploy/.env
```

进入 nano 编辑器后，用方向键移动光标，改这几处：

1. `POSTGRES_PASSWORD=` 后面填刚才生成的**第二行**（16 位那个）
2. `JWT_SECRET=` 后面填刚才生成的**第一行**（32 位那个）
3. `LLM_CONFIG_ENCRYPTION_KEY_ID=` 和 `LLM_CONFIG_ENCRYPTION_KEYS=` 必须填写生产密钥环：先生成 32 字节随机值，转成标准 Base64，JSON 的 key 是密钥 ID。密钥环支持双读单写轮换，旧 ID 在确认迁移完成前必须保留。
4. `DEEPSEEK_API_KEY=`、`LLM_MODEL=`、`LLM_BASE_URL=` 是观察发布期 bootstrap 输入。它们只在模型中心为空时使用一次，管理员配置完成后不会覆盖数据库；观察期后另开任务删除这三个变量。没有真实供应商密钥时 readiness 会失败，不能用伪造结果绕过。
5. 邮件（注册验证码）：不配的话验证码会写在 api 容器日志里（自己用够了）。要真发邮件就填 SMTP 那几行（QQ 邮箱：`SMTP_HOST=smtp.qq.com`，密码填邮箱设置里开的「授权码」），并把 `EMAIL_ENABLED=false` 改成 `true`

> nano 里粘贴：Mac 终端直接 `Cmd+V`。
> 保存退出：按 `Ctrl+O` → 回车 → `Ctrl+X`。

---

## 第 6 步：构建、bootstrap 与 fail-closed 启动（首次约 10~20 分钟）

```bash
cd /opt/aistock
docker compose -f deploy/docker-compose.yml build migrator api worker nginx
docker compose -f deploy/docker-compose.yml up -d postgres redis
docker compose -f deploy/docker-compose.yml run --rm migrator
docker compose -f deploy/docker-compose.yml up -d --no-deps api
docker compose -f deploy/docker-compose.yml exec api python -m app.cli.llm_config readiness
docker compose -f deploy/docker-compose.yml exec api python -m app.cli.llm_config live-smoke --provider deepseek
docker compose -f deploy/docker-compose.yml up -d --no-deps worker nginx
```

首次构建要下载基础镜像、装依赖，比较慢，去喝杯水。migrator 只运行一次；readiness 或真实
DeepSeek smoke 失败时保持流量关闭，不要启动 Worker/Nginx 伪装成功。

**验证（逐条执行）**：

```bash
docker compose -f deploy/docker-compose.yml ps
```

要看到 **5 个容器全部 Up**：aistock-pg、aistock-redis、aistock-api、aistock-worker、aistock-nginx。

```bash
curl http://localhost:8080/api/health
```

看到健康响应还不代表可接流量；必须同时通过 readiness 和真实 DeepSeek smoke。之后再确认
5 个容器全部 Up，才算完成本次启动。

**最后顺手确认 GS-Tracker 没受影响**（应该还是原来那 3 个容器在跑）：

```bash
docker ps --format '{{.Names}}' | grep gs-tracker
```

然后浏览器打开 **http://111.228.23.109:8080**，能看到登录页就部署成功了。

---

## 第 7 步：创建管理员账号

系统第一个账号要手动提升为管理员：

1. 浏览器打开 http://111.228.23.109:8080 → 注册一个账号（用户名比如 `admin`，密码自己设个强的）
2. 如果没配邮件，验证码这样看：

```bash
docker logs aistock-api --tail=50 | grep -i code
```

3. 注册完登录一次，然后回服务器执行提权（把 `admin` 换成你刚注册的用户名）：

```bash
docker exec -it aistock-pg psql -U aistock -c "UPDATE users SET role='admin', tier='A' WHERE username='admin';"
```

看到 `UPDATE 1` 就成了。重新登录后，侧边栏会出现「系统配置」入口。

---

## 第 8 步：配 GitHub 自动部署（push 即上线）

仓库已带 GitHub Actions：本地 push 到 main → 自动跑全部测试 → 全绿后 SSH 上服务器更新。**测试不过不会部署**，这是硬性关卡。
生产发布仍需负责人先完成维护窗口审批；没有明确授权时不要 push 到触发部署的分支。

在你自己的 Mac 上操作：

1. 浏览器打开 https://github.com/zhizhengqin/aistock/settings/secrets/actions
2. 点「New repository secret」，**逐个添加 3 个密钥**（和 GS-Tracker 仓库里的三个值完全一样，同一台服务器）：

| Secret 名 | 内容 |
|---|---|
| `SERVER_HOST` | `111.228.23.109` |
| `SERVER_USER` | `root` |
| `SERVER_SSH_KEY` | 服务器上 `~/.ssh/id_rsa` 私钥全文（部署 GS-Tracker 时生成的那个） |

私钥全文这样拿（在服务器上执行，输出整段复制，包括 `-----BEGIN...` 和 `-----END...` 两行）：

```bash
cat ~/.ssh/id_rsa
```

> 如果提示文件不存在，说明 GS-Tracker 当时用的是别的密钥文件名，执行 `ls ~/.ssh/` 看看，把私钥文件（没有 .pub 后缀的那个）内容复制出来。

**验证**：本机随便改个文件 push 到 main，然后去 https://github.com/zhizhengqin/aistock/actions 看workflow 跑绿，服务器上 `docker ps` 能看到容器刚重启过。

---

## 第 9 步：HTTPS（需要一个域名）

**为什么不能直接给 IP 上 HTTPS**：主流证书机构不给裸 IP 签长期证书，所以需要一个域名。便宜的 .top/.cn 一年十几块钱，阿里云/腾讯云都有卖。

**关于备案**：域名指向国内服务器，**80/443 端口**必须 ICP 备案才能访问（云厂商会拦截）。我们的系统跑在 **8443 端口，不在拦截范围，不备案也能用 HTTPS**。将来如果备了案、想去掉网址里的 :8443，需要动 GS-Tracker 的端口或加统一网关，到时再说。

**9.1 域名解析**：在买域名的控制台加一条 DNS 记录：类型 `A`，主机记录 `aistock`（也可以 `@` 或你喜欢的），记录值 `111.228.23.109`。等 5 分钟生效。下文都用 `aistock.你的域名.com` 举例，记得替换成你自己的。

**9.2 用 DNS 方式申请免费证书**（不占用任何端口，完全不碰 GS-Tracker）：

```bash
curl https://get.acme.sh | sh -s email=你的邮箱@example.com
source ~/.bashrc
```

然后按你的域名 DNS 服务商选一个（在谁那儿买的域名、NS 是谁就用谁）：

```bash
# 阿里云域名：先去控制台「AccessKey 管理」拿一对 Key/Secret，然后：
export Ali_Key="你的AccessKeyId"
export Ali_Secret="你的AccessKeySecret"
~/.acme.sh/acme.sh --issue --dns dns_ali -d aistock.你的域名.com

# 腾讯云/DNSPod 域名：先去 console.dnspod.cn 拿 ID/Token，然后：
export DP_Id="你的ID"
export DP_Key="你的Token"
~/.acme.sh/acme.sh --issue --dns dns_dp -d aistock.你的域名.com
```

看到 `Cert success` 就成了。证书 60 天自动续期，不需要再管。

**9.3 安装证书到 nginx 挂载目录**（域名替换成你的）：

```bash
mkdir -p /data/aistock/nginx/certs/aistock.你的域名.com
~/.acme.sh/acme.sh --install-cert -d aistock.你的域名.com \
  --fullchain-file /data/aistock/nginx/certs/aistock.你的域名.com/fullchain.pem \
  --key-file       /data/aistock/nginx/certs/aistock.你的域名.com/key.pem \
  --reloadcmd     "docker restart aistock-nginx"
```

**9.4 改 nginx 配置启用 443**（这一步在你自己的 **Mac** 上操作——改动进 git，以后自动部署不会冲突）：

用编辑器打开你 Mac 上 aistock 仓库里的 `deploy/nginx.conf`：

- 找到最下面 `# --- HTTPS ...` 那段，把整段 443 server 块的 `#` 注释去掉（每行行首的 `# ` 删掉）
- 里面两处 `your-domain.com` 改成 `aistock.你的域名.com`
- 再把上面 80 端口 server 里的 `# return 301 https://$host:8443$request_uri;` 这行的 `# ` 也删掉（HTTP 自动跳 HTTPS）

然后提交推送（GitHub Actions 会自动部署到服务器，约 3~5 分钟）：

```bash
本机$ cd 你的aistock仓库目录
本机$ git add deploy/nginx.conf && git commit -m "enable https on 8443" && git push
```

> 注意顺序：**必须先做完 9.3 装好证书，再做 9.4**。否则 nginx 找不到证书文件会起不来（真发生了就在服务器把 deploy/nginx.conf 改回去，执行 `docker compose -f deploy/docker-compose.yml restart nginx` 恢复）。

**验证**：浏览器打开 `https://aistock.你的域名.com:8443`，有锁标志、能登录就全部搞定。

---

## 第 10 步：数据库每日备份

```bash
crontab -e
```

在打开的文件**最后加一行**（别动已有的行——GS-Tracker 的定时任务可能也在里面）：

```
30 3 * * * /opt/aistock/deploy/backup.sh >> /data/aistock/backups/cron.log 2>&1
```

每天凌晨 3:30 自动备份，保留 14 天，备份文件在 `/data/aistock/backups/`。第一周记得瞄一眼有没有新文件。

恢复或升级前的维护窗口必须由负责人明确授权：先暂停新的 AI 任务，记录并处理 pending/running
任务；备份 PostgreSQL 和 `deploy/.env`；停止旧 API、Worker、Nginx；执行一次 migrator 并
核对完整 Alembic heads；再按健康 → readiness → DeepSeek 真实 smoke → Worker → Nginx 的
顺序启动。任何一步失败都保持 fail-closed，不让旧 Nginx 继续导流。恢复数据库备份后只能使用
与该 schema 匹配的上一版本镜像，并重新执行 readiness，不能把新版本任务重放到旧表结构。

Worker 每日北京时间 01:00 执行 90 天内部 payload 清理：仅清空已终态、无 pending/locked
outbox、无 reserved reservation、无 live lease 的调用响应正文；保留 hash、schema、token、
费用、错误、最终报告、审计和模型配置。清理后原任务不再重放，人工恢复只能新建 task ID。
Redis 只负责队列/缓存，Token reservation 与 settled 用量以 PostgreSQL 为准，Redis 重启不会
重置额度。

---

## 故障排查

网站打不开或行为异常时，**整段复制**下面命令到服务器执行，把输出发给我：

```bash
cd /opt/aistock
echo "===== 1. 容器状态 ====="
docker compose -f deploy/docker-compose.yml ps
echo "===== 2. api 日志（最后 50 行） ====="
docker logs aistock-api --tail=50 2>&1
echo "===== 3. worker 日志（最后 30 行） ====="
docker logs aistock-worker --tail=30 2>&1
echo "===== 4. 关键文件检查（密钥只显示长度） ====="
ls -la deploy/.env 2>&1
python3 -c "
for line in open('deploy/.env'):
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    k, v = line.split('=', 1)
    if 'PASSWORD' in k or 'SECRET' in k or 'KEY' in k:
        print(f'{k} = 已设置(长度{len(v)})' if v else f'{k} = 【空！需要填】')
    else:
        print(f'{k} = {v}')
"
echo "===== 5. 健康检查 ====="
curl -s http://localhost:8080/api/health 2>&1
echo "===== 6. 磁盘和内存 ====="
df -h / | tail -1
free -h | head -2
echo "===== 7. GS-Tracker 是否健在 ====="
docker ps --format '{{.Names}}\t{{.Status}}' | grep gs-tracker
```

**常见问题速查**：

| 症状 | 大概率原因 | 处理 |
|---|---|---|
| 报错 `no such service: deploy/.env` | 命令最后误加了 `deploy/.env` | 删掉它，完整命令是 `docker compose -f deploy/docker-compose.yml up -d --build`（.env 会被自动读取，不用写在命令里） |
| 浏览器打不开 :8080 | 安全组没放行 8080 | 重做第 3 步 |
| 容器一直 Restarting | .env 没配或密码不对 | 看诊断第 4 节，重做第 5 步 |
| curl :8080 拒绝连接 | aistock-nginx 没起来 | `docker logs aistock-nginx --tail=30` 看原因 |
| 提示端口被占用 | 别的程序抢了 8080/8443 | `ss -tlnp \| grep 8080` 看是谁，发给我 |
| 注册收不到验证码 | 没配 SMTP | 验证码在日志里：`docker logs aistock-api --tail=100 \| grep "EMAIL-DEV"`（日志里是中文「验证码」，grep code 搜不到） |
| AI 报告一直失败 | DeepSeek key 无效、余额不足或模型未验证 | 查看任务错误码，检查模型中心的测试/启用/默认状态和 readiness；不要绕过真实 smoke |
| 首页行情不更新 | 数据源接口变了 | `docker logs aistock-worker --tail=100` 找红色报错 |
| GS-Tracker 挂了 | 正常来说不会被影响 | `cd ~/gs-tracker && bash deploy/update.sh` 重启它 |

---

## 命令速查卡

```bash
# 进服务器
ssh root@111.228.23.109

# 看 5 个容器状态
docker compose -f /opt/aistock/deploy/docker-compose.yml ps

# 手动更新部署（和 GitHub Actions 执行的是同一套动作）
cd /opt/aistock && git pull origin main && docker compose -f deploy/docker-compose.yml up -d --build

# 看日志（实时，Ctrl+C 退出）
docker logs -f aistock-api
docker logs -f aistock-worker

# 全部重启（不影响 GS-Tracker）
docker compose -f /opt/aistock/deploy/docker-compose.yml restart

# 磁盘清理（只清 dangling 镜像，两套系统都不受影响）
docker image prune -f
```

---

## 部署完成自查清单（逐项打勾）

- [ ] http://111.228.23.109:8080 能打开登录页
- [ ] 注册、登录正常，管理员账号侧边栏能看到「系统配置」
- [ ] 首页行情刷新正常
- [ ] AI 分析能出报告（必须通过真实模型 smoke，结果仅供参考）
- [ ] http://111.228.23.109（GS-Tracker）**依然正常**
- [ ] GitHub push 后 Actions 全绿、服务器自动更新
- [ ] `/data/aistock/backups/` 第二天有备份文件
- [ ] （可选）https://域名:8443 有锁标志
