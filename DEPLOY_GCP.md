# GCP Compute Engine 部署

推荐在 Compute Engine VM 上以 Docker Compose 运行；这是有状态的单容器（内含 Mihomo 与 Playwright Chromium），不推荐 Cloud Run：进程生命周期和持久卷不适合 worker、缓存与浏览器任务。

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
git clone https://github.com/leeflouring/clash-ip-checker.git
cd clash-ip-checker
docker compose config
docker compose pull
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8000/health
```

仅发布 TCP 8000；Mihomo 7890/9090 仅 loopback。GCP 防火墙应限制为办公网/VPN 来源，不建议 `0.0.0.0/0`。公网请使用 TLS 反向代理并设置 `API_TOKEN`。

升级前备份 `clash-data` volume，更新 `main` 分支代码后执行 `docker compose pull && docker compose up -d`。回滚时通过 `IMAGE_TAG` 指定旧的 `sha-...` 镜像标签并重新 `up -d`，必要时恢复 volume；旧 `./data` 需先迁移到新 volume 的 `/data/jobs`。完整环境变量、健康检查和排障见 [README.md](./README.md) / [README_EN.md](./README_EN.md)。
