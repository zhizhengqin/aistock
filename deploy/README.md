# 睿见投研 · 部署手册（京东云）

> 给技术小白的全流程部署指引。每一步照抄命令即可，遇到报错把原文发给 AI 助手。
>
> **服务器上已运行 GS-Tracker（占用 80/443）**：本系统对外端口为 **8080（HTTP）/ 8443（HTTPS）**，
> compose 项目名固定为 `aistock`，两套系统互不影响。完整手把手教程见仓库根目录 `DEPLOY.md`。

## 一、服务器准备（一次性）

1. 京东云控制台买云主机：**4C16G / Ubuntu 24.04 / 系统盘 100G**，记下公网 IP。
2. 安全组（防火墙）放行：**8080（HTTP）、8443（HTTPS）、22（SSH）**；若同机还有其他系统，保留其原有端口。
3. SSH 登录后安装 Docker：

```bash
ssh root@你的服务器IP
curl -fsSL https://get.docker.com | bash
```

4. **配置 Docker 镜像加速器**（国内服务器直连 Docker Hub 很慢/不通，必做）：

```bash
mkdir -p /etc/docker
cat > /etc/docker/daemon.json << 'JSON'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://dockerproxy.net"
  ]
}
JSON
systemctl restart docker
```

## 二、首次部署

```bash
# 1. 拉代码
git clone https://github.com/你的名字/aistock.git /opt/aistock
cd /opt/aistock

# 2. 配置环境变量（密码、密钥）
cp deploy/.env.example deploy/.env
openssl rand -hex 32        # 把输出填进 deploy/.env 的 JWT_SECRET
python deploy/ensure_llm_keyring.py deploy/.env  # 原子生成/校验 LLM 密钥环和 DataHub 32 字节密钥
nano deploy/.env            # 填数据库、JWT、LLM 加密密钥环和观察期 bootstrap 输入

# 3. 先只执行 migrator（首次构建约 10-15 分钟）
docker compose -f deploy/docker-compose.yml build migrator api worker nginx
docker compose -f deploy/docker-compose.yml up -d postgres redis
docker compose -f deploy/docker-compose.yml run --rm migrator

# 4. 启动 API，确认健康、readiness 和真实 DeepSeek smoke 后再恢复流量
docker compose -f deploy/docker-compose.yml up -d --no-deps api
docker compose -f deploy/docker-compose.yml exec api python -m app.cli.llm_config readiness
docker compose -f deploy/docker-compose.yml exec api python -m app.cli.llm_config live-smoke --provider deepseek
docker compose -f deploy/docker-compose.yml up -d --no-deps worker nginx

# 5. 验证
curl http://localhost:8080/api/health
# 看到健康响应后，再访问浏览器；readiness 或真实 smoke 失败时保持流量关闭
docker compose -f deploy/docker-compose.yml ps   # 5 个容器都该是 Up
```

浏览器打开 `http://服务器IP:8080` 就能看到系统。

`deploy/.env` 中的 `DEEPSEEK_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL` 只是观察发布期的兼容
bootstrap 输入：仅在模型中心为空时消费一次，管理员保存模型后不会覆盖数据库配置。生产还必须
配置 `LLM_CONFIG_ENCRYPTION_KEY_ID` 和 `LLM_CONFIG_ENCRYPTION_KEYS`（32 字节密钥的 Base64
JSON）。密钥环支持双读单写轮换；观察期结束后将另开任务删除这三个旧 bootstrap 变量。
`DATAHUB_CONFIG_ENCRYPTION_KEY` 是 PostgreSQL DataHub 凭据的独立 32 字节十六进制主密钥，
由上面的脚本生成并以 0600 权限写入；migrator、api、worker 三个容器都会强制读取它。不要把
真实 `deploy/.env` 提交到仓库，也不要把该密钥写入日志或 API 响应。
系统没有假数据开关；没有可用供应商时 readiness/smoke 会失败，不能用伪造报告绕过。

## 三、日常更新（推到 GitHub 后自动部署）

仓库已带 GitHub Actions（`.github/workflows/deploy.yml`）：
**本地 push 到 main → 自动跑全部测试 → 全绿后 SSH 上服务器自动更新**。
触发生产发布前必须先获得负责人明确授权并完成维护窗口准备；没有授权时只运行本地验证，不要 push 到触发部署的分支。

首次使用需在 GitHub 仓库 → Settings → Secrets and variables → Actions 添加三个密钥：

| Secret 名 | 内容 |
|---|---|
| `SERVER_HOST` | 服务器公网 IP |
| `SERVER_USER` | `root` |
| `SERVER_SSH_KEY` | 本机 `~/.ssh/id_rsa` 私钥全文（服务器 `authorized_keys` 里要有对应公钥） |

服务器首次要允许 GitHub Actions 拉代码：

```bash
cd /opt/aistock
git config --global credential.helper store   # 私有仓库需配 token；公开仓库跳过
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_rsa  # 如果还没有密钥
cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys
```

## 四、HTTPS（8443 端口，免备案可用）

> 完整步骤见根目录 `DEPLOY.md` 第 9 步。要点：域名 A 记录指向服务器 IP →
> acme.sh 用 **DNS-01**（dns_ali / dns_dp，不占任何端口，不碰 GS-Tracker）签发证书 →
> 安装到 `/data/aistock/nginx/certs/你的域名/` → 编辑 `deploy/nginx.conf` 取消 443 server 块注释并替换域名 →
> `docker compose -f deploy/docker-compose.yml restart nginx`。
> 访问地址为 `https://你的域名:8443`。国内云对未备案域名只拦截 80/443，8443 不受影响。

## 五、数据库每日备份

```bash
crontab -e
# 加一行（每天凌晨 3:30 备份，保留 14 天）：
30 3 * * * /opt/aistock/deploy/backup.sh >> /data/aistock/backups/cron.log 2>&1
```

备份文件在 `/data/aistock/backups/`，每周瞄一眼文件是否在增长。

恢复方法（需要时）：

```bash
gunzip -c /data/aistock/backups/aistock_日期.sql.gz | docker exec -i aistock-pg psql -U aistock aistock
```

恢复前先停止 API、Worker 和 Nginx，确认备份日期与迁移版本；恢复后重新运行
`docker compose -f deploy/docker-compose.yml run --rm migrator`，再按健康 → readiness →
真实 smoke → Worker → Nginx 的顺序启动。不要在未验证 schema 时恢复旧流量。

## 六、维护窗口与 LLM 运行规则

生产升级必须由负责人明确授权。维护窗口内先暂停新的 AI 任务，记录并处理 pending/running
任务，备份 PostgreSQL 和 `deploy/.env`，停止旧 API/Worker，执行一次 migrator 并核对完整
Alembic heads；API 健康、readiness 和 DeepSeek smoke 全部成功后才启动 Worker 和 Nginx。
任一步失败都保持 fail-closed（停止流量），不要让旧 Nginx 继续导流到未验收的 API。

Worker 每日北京时间 01:00 清理超过 90 天且已终态、无锁和无活动租约的内部模型响应正文；
只清空调用尝试的响应 JSON，最终报告、审计、额度账本和模型配置永久保留。清理后原任务不重放，
恢复只能新建 task ID。Redis 是队列和缓存，PostgreSQL 才是 Token 额度账本；Redis 重启不会
重置已预留或已结算额度。

## 七、上线检查清单（逐项打勾）

- [ ] 注册/登录正常
- [ ] 首页行情刷新正常
- [ ] 每个 AI 模块各跑通一次（分析报告页能看到结果）
- [ ] 定时任务连续 2 天无失败（通知铃铛无失败告警）
- [ ] 备份脚本在跑（`/data/aistock/backups/` 有新文件）
- [ ] 手机浏览器（或 Chrome 开发者工具切 390px）逐页面检查无错位
- [ ] 所有 AI 报告底部有免责声明
- [ ] DeepSeek 账户设了消费上限
- [ ] 安全组只开 8080/8443/22（加上 GS-Tracker 原有的 80/443）

## 八、常见故障自救

| 症状 | 处理 |
|---|---|
| 网站打不开 | `docker compose -f deploy/docker-compose.yml ps` 看哪个容器挂了 → `... restart 容器名` |
| 首页行情不更新 | `docker logs aistock-worker --tail=100`，多半是数据源接口变了 |
| AI 报告失败 | 看任务记录的稳定错误码；检查模型是否已测试、启用、设默认，以及 DeepSeek 余额和限流 |
| 磁盘满 | `docker system prune -a` 清旧镜像；清旧备份 |
