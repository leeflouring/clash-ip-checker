# Docker delivery documentation gap audit

status: complete

## Summary

当前文档仍是旧 docker 分支说明：只有中文 `README.md`，没有 `README_EN.md`；Compose、路径、端口和命令与 07-27-docker-delivery 设计的非 root、单 `/data` volume、健康检查/监督进程安全默认值不一致。建议以 Compose quick-start 为首段，README 中英文同步同一功能矩阵，并把旧主分支比较和历史版本内容保留为历史说明。

## Evidence and minimal edits

- 文档存在：`README.md`、`CHANGELOG.md`、`DEPLOY_GCP.md`、`docs/index.html`、`docker-compose.yml`、`config.yaml`；缺失：`README_EN.md`、独立 troubleshooting 文档。任务设计要求中英 README、changelog、deployment/troubleshooting（`.trellis/tasks/07-27-docker-delivery/prd.md:13-14`, `design.md:34-35`）。最小改法：新增英文 README（或明确链接），在 README/README_EN 同步章节。
- Compose 仍 `./data:/root/.config/mihomo/data`、`DATA_DIR=/root/.config/mihomo/data`、`restart: always`，且无 healthcheck（`docker-compose.yml:7-13`）。与设计要求 named `/data`、非 root、health/restart、仅 web 端口（`design.md:22-24`）不符。最小改法：文档命令改为 `docker compose`；示例使用实际 Compose volume 名称挂载 `/data`，记录仅发布 `8000:8000`、健康状态及数据持久化；不要继续宣传宿主 `./data` 或 `/root` 路径。
- README 快速开始要求 `git checkout docker`、`docker-compose up -d --build`（`README.md:60-69`），而任务交付命令是 `docker compose config/build/up/ps`（`implement.md:10-18`）。最小改法：统一插件命令和分支说明（若当前分支即 docker，删除 checkout 步骤或标记历史）。
- README 宣称 URL 转换 `/check?url=...`、Web `/ipcheck`（`README.md:12-20`, `74`），但当前 API 主要是 `/api/jobs`、`/api/jobs/{id}`、events/cancel、node edit/delete/recheck、raw/export、config/status（`main.py:485-754`）。旧 `/check` 行为需确认是否仍由 routes 800+ 提供；若保留兼容路由，文档应标为兼容入口，否则改为 Web 工作台/API jobs 示例。`/ipcheck` 页面仍由前端存在（`templates/index.html:16`）。
- 前端实际支持：URL 或粘贴/上传 Clash YAML（`templates/index.html:53-72`）；fast/browser 模式与 headless（`templates/index.html:80-87`, `static/js/app.js:551-554`）；实时事件、取消（`static/js/app.js:298`, `393`）；表格编辑、删除、单节点重检（`static/js/app.js:405-449`）；原始 YAML、复制/下载 YAML、CSV、Clash deep-link（`templates/index.html:153-160`, `static/js/app.js:462-539`）。README 当前仅提 URL、Web、缓存/数据源，缺上述 YAML/table/raw/CSV/browser/edit/delete/recheck/deep-link 说明。最小改法：中英文新增“输入与结果工作台”功能矩阵和限制（结束态才能操作节点、Clash 客户端需支持 `clash://install-config`）。
- 当前输入默认拒绝内网及带凭据 URL 的 UI 文案（`templates/index.html:64`）；配置代码支持 `ALLOW_PRIVATE_SUBSCRIPTIONS`，默认 false（`core/config.py:87`）。任务还要求 `API_TOKEN` Bearer/兼容 query 保护 job/result/subscription routes，health/static 公共（`design.md:29-31`）；README/DEPLOY_GCP 完全未记录。最小改法：补安全默认、token 配置、凭据/订阅隐私、服务暴露风险和仅 loopback Mihomo controller/proxy 的说明。
- 环境/默认值漂移：`main.py` 默认 `DATA_DIR=<app>/data`、`CLASH_API_URL=http://127.0.0.1:9090`、`PORT=8000`（`main.py:38-44`, `960-962`）；`core/config.py` 读取 `CONFIG_PATH`（`core/config.py:28`）和 `ALLOW_PRIVATE_SUBSCRIPTIONS`（`:87`）。README 表格写 `REQUEST_TIMEOUT` 默认 10（`README.md:95`），而 `config.yaml` 为 15（`config.yaml:20`），并暴露 `SHOW_ADVANCED_SETTINGS`（`config.yaml:34`）但未列环境映射。最小改法：建立唯一环境变量表，核准 timeout 真实默认，列 `DATA_DIR/CONFIG_PATH/CLASH_API_URL/PORT/MAX_QUEUE_SIZE/MAX_AGE/REQUEST_TIMEOUT/SOURCE/FALLBACK/ALLOW_PRIVATE_SUBSCRIPTIONS/API_TOKEN`。
- `DEPLOY_GCP.md` 仍指导 `sudo docker-compose`、`git checkout docker`（`:23-32`），并说 Cloud Run 注入 `PORT`（`:57`）；未涵盖 Compose health, named volume, non-root, loopback listeners、升级/回滚、隐私或故障排查。最小改法：保留 GCP 历史/平台特定章节，命令改 `docker compose` 并交叉链接通用 quick start；补健康失败、Mihomo 崩溃、权限/volume、端口和 rollback 步骤。
- `CHANGELOG.md` 仅有旧 v1.1.0 及 Mihomo v1.19.18 等历史条目（`:1-45` 附近），没有本次 Docker hardening 的版本/迁移说明。最小改法：新增当前交付条目，明确 breaking changes（`/data` volume、非 root、只发布 8000、健康门槛、环境名）。旧版本条目保留。
- `docs/index.html` 是营销落地页，仍写“自动切换节点”“无需人工干预”（`:89`, `175`），无安全、隐私、浏览器模式成本、升级回滚；不应承担运维文档。最小改法：保留历史/营销描述，增加指向 README 或部署文档链接，不在此复制完整命令。
- 故障排查/升级回滚缺口：任务明确要求健康 ready、杀死 Mihomo 后 `/health` 失败且容器退出、TERM 同时收停、重建后 `/data` 保留（`prd.md:18-22`, `implement.md:23-25`）。现有文档无任何检查命令或回滚步骤。最小改法：加入 `docker compose ps`, `logs`, `curl /health`, `docker compose stop`, `docker compose up -d --build`、volume 备份/恢复和旧镜像回滚；说明 checked-cache 可再生、输入订阅/持久数据需备份。

## Gaps / uncertainty

- 未在本次审计中执行 Docker 或 HTTP；因此“`/check` 是否仍由 800+ 路由兼容提供”仅由 README 与当前主路由列表冲突推断，需实现侧核验后决定保留或删除旧命令。
- `Dockerfile`/`entrypoint.sh` 当前仍写 `/root/.config/mihomo`（`entrypoint.sh:12`），但交付任务要求改为非 root `/data`；文档应在实现落地后按最终路径复核。
