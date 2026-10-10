# 本地开发

需要 Python 3.11+、Node.js 22+。本地开发使用 SQLite、独立凭据密钥和持续 worker。

## 初始化

Linux / macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cp -n backend/.env.example backend/.env
python backend/manage.py initialize_credential_key
python backend/manage.py migrate
python backend/manage.py createsuperuser
npm ci
```

Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
if (-not (Test-Path backend\.env)) { Copy-Item backend\.env.example backend\.env }
python backend\manage.py initialize_credential_key
python backend\manage.py migrate
python backend\manage.py createsuperuser
npm ci
```

把 `backend/.env` 中的 `DJANGO_SECRET_KEY` 换为随机值。保留已有数据库与密钥，首次初始化命令不会覆盖已有密钥。

## 运行

在激活同一个虚拟环境的三个终端分别启动：

```bash
python backend/manage.py runserver 127.0.0.1:8000
```

```bash
python backend/manage.py process_review_queue --watch
```

```bash
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

打开 `http://127.0.0.1:5173`。开发前后端统一使用 `127.0.0.1`，避免 Cookie 来源混用；默认 API 为 `http://127.0.0.1:8000/api`。需要其他 API 时复制根 `.env.example` 为 `.env.local`，设置 `VITE_API_BASE`。生产镜像构建为相对 `/api`。

## 验证

```bash
npm run test:ci
npm run build
python backend/manage.py test review config --noinput
python -m unittest discover -s docker/tests
python backend/manage.py makemigrations --check --dry-run
python backend/manage.py check
python backend/scripts/delivery_smoke.py
```

Django test runner 不加载开发 `.env`，使用临时凭据密钥；已有 shell 环境变量仍可显式设置。隔离联调脚本创建新数据库，阻断外部 HTTP，不使用现有账号或连接。

容器验证（Bash / WSL）：

```bash
docker build -t cw-cr:smoke .
bash scripts/container_smoke.sh cw-cr:smoke
```

脚本创建并清理专用测试容器和卷，验证同源登录、CSRF、权限、SPA、数据库及密钥在重建容器后保留。可选第三方引擎不进入默认镜像；真实模型与远端集成应在明确配置的部署环境验收。
