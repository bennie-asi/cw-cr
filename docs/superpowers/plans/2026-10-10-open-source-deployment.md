# 智审台容器化与开源交付实施计划

**Goal:** 在 `bennie-asi/cw-cr` 发布可快速部署、可通过 Actions 构建镜像的 MIT 项目。

**Architecture:** 单容器托管 Nginx、Gunicorn 与 worker，持久化 `/data`；React 请求同源 `/api`。公开 GHCR 镜像与源码构建使用相同 Dockerfile。

**Tech Stack:** React/TypeScript、Django、Python 3.12、Node 22、Nginx、Supervisor、Docker Compose、GitHub Actions。

按用户已批准范围在当前会话逐项执行；不创建其他聊天或委派探索。

1. 为 `backend/config/test_settings.py` 添加 SQLite 绝对路径、文件密钥、HTTP Cookie 与独立 CSRF 来源回归。执行 `python backend/manage.py test config --noinput`，观察失败后修改 `backend/config/settings.py`。测试模式跳过 `.env`，普通运行维持页面/环境配置优先级。
2. 创建 `Dockerfile`、`.dockerignore`、`compose.yaml`、`compose.build.yaml`、`.env.docker.example`。容器以 UID 10001 运行，入口先初始化密钥、执行迁移，再启动 `docker/supervisord.conf` 中三个进程；Nginx 只代理 `/api/`，其余 SPA fallback，静态资源独立缓存。
3. 为 `docker/bootstrap.py` 的首次生成、不覆盖、文件权限和非法已有密钥添加测试，再实现原子密钥初始化。创建 `docker/healthcheck.py` 检查 HTTP 与全部受托管进程。
4. 创建 `scripts/container_smoke.py` 和 `scripts/container_smoke.sh`，使用临时 Docker 卷、合成账号，验证登录/CSRF、项目写入、退出、拒绝无授权访问、SPA、进程、重启后数据与密钥一致。执行 `wsl -d Ubuntu-24.04 -- docker build -t cw-cr:smoke /mnt/c/dev/projects/other/cw-cr` 后运行冒烟脚本。
5. 新增 `.github/workflows/ci.yml` 和 `docker.yml`，使用官方 Actions 的固定提交；前者运行前后端回归与容器冒烟，后者完成验证后通过 `GITHUB_TOKEN` 发布 GHCR，支持手动输入镜像标签和版本标签。
6. 用通用 `example-team`、`demo-api` 等名称替换内部示例；更新截图、README、`docs/deployment.md`、`docs/development.md`、贡献/安全文档和 MIT。更新忽略规则与 LF 规范，保留本地秘密与历史档案。
7. 执行完整测试、生产构建、迁移检查、隔离联调、Compose 配置检查和 Docker 冒烟；审核 `git diff --cached` 及秘密扫描。提交可发布源码，创建公开仓库并推送 main，验证 Actions 和 GHCR 匿名 manifest 获取。

关键设置实现：`sqlite:////data/db.sqlite3` 解析为 `/data/db.sqlite3`；`DJANGO_SECRET_KEY` 优先于 `DJANGO_SECRET_KEY_FILE`；`DJANGO_SECURE_COOKIES` 缺省沿用 `not DEBUG`；`DJANGO_CSRF_TRUSTED_ORIGINS` 缺省沿用 CORS 来源。Compose 的预构建镜像默认为 `ghcr.io/bennie-asi/cw-cr:latest`，本地构建使用 `docker compose -f compose.yaml -f compose.build.yaml up -d --build`。
