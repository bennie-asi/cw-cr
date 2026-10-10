# 部署指南

## 容器与持久化

单个应用容器托管 Nginx、Gunicorn 和评审 worker，监听 8080，同源提供页面与 `/api/`。Supervisor 自动重启异常退出的子进程；健康检查同时验证 HTTP、数据库和三个进程。

应用以 UID/GID `10001` 运行。默认命名卷保存：

| 路径 | 内容 |
| --- | --- |
| `/data/db.sqlite3` | 账号、项目、配置、评审和队列 |
| `/data/secrets/django.key` | Django 会话签名密钥 |
| `/data/secrets/credential.key` | 数据库凭据的独立 Fernet 密钥 |
| `/data/runtime/locks` | 同机进程锁 |

首次启动初始化密钥并执行迁移；后续启动保留已有密钥。已有密文而凭据密钥丢失时会拒绝生成替代密钥。不会自动创建管理员或演示数据。

API 与 worker 必须位于同一应用主机并共享锁目录。即使使用 PostgreSQL，也不要跨主机扩容应用容器。Compose 支持外部数据库，本模板默认 SQLite，不引入额外数据库服务。

## 启动与管理

```bash
docker compose up -d --wait
docker compose exec app python manage.py createsuperuser
docker compose ps
docker compose logs -f --tail=100 app
docker compose exec app supervisorctl -c /app/docker/supervisord.conf status
```

本地构建：

```bash
docker compose -f compose.yaml -f compose.build.yaml up -d --build --wait
```

仅停止服务用 `docker compose down`。`down -v` 会删除命名卷及全部数据，不用于常规升级。

## 环境配置

复制 `.env.docker.example` 为 `.env`。已有文件时保留并编辑，不覆盖现有秘密。

| 变量 | 默认 / 用途 |
| --- | --- |
| `CW_CR_IMAGE` | `ghcr.io/bennie-asi/cw-cr:latest`；正式部署建议固定版本标签或 digest |
| `PORT` / `BIND_ADDRESS` | `8080` / `127.0.0.1`，主机入口 |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1`；加入实际域名，不含协议或端口 |
| `PUBLIC_BASE_URL` | 完整公开来源，用于展示 Webhook 地址；改端口时同步修改 |
| `DJANGO_SECURE_COOKIES` | 本地 HTTP 为 `false`，公网 HTTPS 为 `true`；DEBUG 始终关闭 |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | 实际公开 HTTPS 来源，多个用逗号分隔 |
| `DJANGO_TRUST_PROXY` | 是否信任反向代理的 `X-Forwarded-Proto`；仅在受控代理后启用 |
| `DATABASE_URL` | `sqlite:////data/db.sqlite3`，四个斜线表示绝对路径；支持 PostgreSQL |
| `DJANGO_SECRET_KEY` | 可选外部注入，优先于镜像中的持久化文件 |
| `CREDENTIAL_ENCRYPTION_KEY` | 可选有效 Fernet 密钥，优先于持久化文件 |

直接运行镜像时还可用 `DJANGO_SECRET_KEY_FILE`、`CREDENTIAL_ENCRYPTION_KEY_FILE` 指定受保护的挂载文件。Bind mount 需为 UID 10001 提供写入权限；优先采用自动初始化权限的命名卷。

## 通过 HTTPS 域名部署

在主机反向代理配置有效证书，将请求转发至仅绑定回环地址的 `8080`。示例 `.env`：

```dotenv
DJANGO_ALLOWED_HOSTS=review.example.com,localhost,127.0.0.1
PUBLIC_BASE_URL=https://review.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://review.example.com
DJANGO_SECURE_COOKIES=true
DJANGO_TRUST_PROXY=true
```

外层 Nginx 的已配置证书 HTTPS server 中：

```nginx
server_name review.example.com;
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 180s;
}
```

反向代理应覆盖客户端传入的协议头。健康检查使用 `localhost`，请保留该 Allowed Host。当前模板部署在域名根路径，不支持子路径。首次配置后验证登录、退出、页面刷新、GitLab 回调和企微消息。

## 使用 PostgreSQL

把 `DATABASE_URL` 改为 URL 编码后的连接信息，例如：

```dotenv
DATABASE_URL=postgresql://review:URL_ENCODED_PASSWORD@db.example.com:5432/cwcr?sslmode=require
```

新数据库由入口自动迁移。已有 SQLite 数据不会自动迁入 PostgreSQL；切换前暂停服务，按数据库迁移流程验证账号、项目、历史和凭据。仍保留 `/data` 中的密钥与锁目录。连接主机服务时 Compose 提供 `host.docker.internal` 映射；容器里的回环地址不能访问主机网关。

## 备份、升级与恢复

升级前停止写入和 worker，保护数据库与独立密钥备份。以下命令使用默认 Compose 卷，通过当前应用容器配置挂载卷，适用于 SQLite：

```bash
docker compose stop app
docker compose run --rm --no-deps --entrypoint tar app -C /data -czf - db.sqlite3 > database-backup.tar.gz
docker compose run --rm --no-deps --entrypoint tar app -C /data -czf - secrets > keys-backup.tar.gz
docker compose pull
docker compose up -d --wait
```

二进制重定向请在 Bash 或 PowerShell 7.4+ 执行。数据库与密钥备份应存放在不同、访问受限的位置。PostgreSQL 使用其一致性备份工具；不要用上述 SQLite 命令替代。

恢复时先停止 app，向同一个命名卷恢复数据库及与它对应的原密钥，再启动相同版本镜像并验证读取。凭据密钥不能直接替换；Django 密钥变更会使已有会话失效。旧版本数据库升级前先对副本执行迁移，确认凭据可解密。无需也不要删除运行锁目录来“解锁”。

## 常见问题

- **任务保持排队**：检查 `supervisorctl status` 的 worker 及容器日志。
- **登录或 CSRF 失败**：确认浏览器域名、HTTPS、Allowed Hosts、可信来源与 Cookie 配置一致。
- **模型网关连不上**：用主机映射地址或可访问的服务域名，检查网关监听地址和防火墙。
- **凭据解密失败**：恢复与数据库对应的原 Fernet 密钥，不生成替代密钥。
- **镜像拉取 denied**：检查 Package 是否 Public；Fork 的新包首次发布默认可能是 Private。
- **升级失败**：保留备份和旧镜像，停止新进程后恢复已验证的数据库/密钥组合。

参考：[Django 部署检查](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)、[Docker Compose](https://docs.docker.com/compose/)、[GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。
