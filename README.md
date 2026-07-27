# Clash IP Checker

基于 FastAPI 与 Mihomo 的订阅节点 IP 检测服务。Mihomo 1.19.18，镜像支持 amd64/arm64；browser 模式使用 Playwright 的服务器 headless shell。

## Compose 快速开始

需要 Docker Compose plugin（命令为 `docker compose`）：

```bash
docker compose config
docker compose build
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8000/health
docker compose logs --tail=200 clash-checker
```

服务发布 `8000`；Mihomo mixed/controller 仅 loopback（7890/9090），无 DNS listener。named volume `clash-data` 挂载 `/data`（jobs `/data/jobs`、Mihomo `/data/mihomo`），容器以 UID 10001、只读 rootfs 运行并 `restart: unless-stopped`。

## 使用

打开 [`http://127.0.0.1:8000/`](http://127.0.0.1:8000/)，可输入订阅 URL、粘贴或上传 YAML，选择 fast/browser。远程部署请把 `127.0.0.1` 换成服务器实际 IP；不要用监听地址 `0.0.0.0`。结果支持表头筛选和排序、失败原因、表格或 raw YAML，导出选中/全部 YAML 或 CSV、复制、Clash deep-link。任务结束后可编辑、删除、单节点 recheck；运行中可 cancel，断线可 reconnect。最近终态结果以只读快照保存在 `/data/jobs/history.json`，可查看或删除，最多保留 `MAX_JOBS` 条。跳过关键词留空时使用服务器默认值，自动略过剩余流量、重置和到期等订阅信息节点。`/ipcheck` 与旧版 `GET /check?url=` 兼容入口保留。

容器不含 GUI 或桌面环境，browser 模式固定 headless，不支持 headed；headless shell 比完整 Chromium 镜像小，但仍显著大于仅支持 fast 模式的基础镜像。兼容订阅入口会按内容与有效选项复用缓存；单 Mihomo worker 限制吞吐，browser 模式更耗内存。浏览器 sessionStorage 仅存 job ID，URL 和 API 令牌不持久化。

启用回退时，fast 模式会在主要 Ping0/IPPure 失败后尝试另一个来源，最后匿名请求第三方 IPQuery；browser 解析失败时也会进入该 fast 回退链。IPQuery 只能提供风险与机房/移动属性，不能证明原生或广播，因此原生性显示为“未知”。关闭回退不会请求 IPQuery。

## 配置

| 环境变量 | Compose 默认值 | 说明 |
| --- | --- | --- |
| `DATA_DIR` | `/data/jobs` | 任务与结果目录 |
| `CONFIG_PATH` | `/app/config.yaml` | 应用配置文件 |
| `CLASH_API_URL` / `PORT` | `http://127.0.0.1:9090` / `8000` | 内部控制器 / Web 端口 |
| `MAX_QUEUE_SIZE` / `MAX_AGE` | `10` / `360` | 队列上限 / 兼容订阅缓存秒数 |
| `REQUEST_TIMEOUT` / `SOURCE` / `FALLBACK` | `15` / `ping0` / `true` | 检测策略 |
| `MAX_SUBSCRIPTION_BYTES` / `MAX_REDIRECTS` | `5242880` / `3` | 下载边界 |
| `JOB_TTL` / `MAX_JOBS` | `3600` / `100` | 内存任务 TTL / 持久历史条数上限 |
| `ALLOW_PRIVATE_SUBSCRIPTIONS` | `false` | 明确允许私网订阅目标 |
| `SHOW_ADVANCED_SETTINGS` / `API_TOKEN` | `false` / 空 | UI 与访问控制 |

设置 `API_TOKEN` 后 job/result/subscription/history 路由需 `Authorization: Bearer …`，health、静态资源和 UI 配置仍公开；UI token 只在内存中，并改用授权轮询。此时 Clash deep-link 和直接订阅 `/check` 无法携带 Bearer，请下载 YAML 导入或在 TLS 反向代理层提供合适认证。安全默认拒绝 SSRF 内网地址和带 credentials 的 URL。

历史文件只包含脱敏标签、终态统计和已公开的结果行；不会写入订阅 URL、API token、原始 YAML、代理配置或凭据。

## 升级、回滚与排障

升级前备份 Compose volume `clash-data`，拉取代码后执行 `docker compose build --pull && docker compose up -d`。回滚时把 Compose image 改回旧标签后重新 `up -d`；必要时恢复 volume。旧版 `./data` 内容请先复制到新 volume 的 `/data/jobs`。使用 `docker compose ps`、`logs` 与 `/health` 检查 Mihomo；browser 内存不足请增加内存，权限错误确认 UID 10001 可写 `/data`，队列满则调低提交速率或增大 `MAX_QUEUE_SIZE`。公网部署请限制防火墙来源。

## 架构构建

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t clash-ip-checker:local .
```

GCP 部署见 [DEPLOY_GCP.md](./DEPLOY_GCP.md)，英文版见 [README_EN.md](./README_EN.md)。
