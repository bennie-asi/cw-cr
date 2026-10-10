# 接入指南

## GitLab 与项目

登录后在系统设置中保存 GitLab 地址、API Token、Webhook Secret，测试连接；在项目管理中添加 `group/project` 仓库，完成只读连接检查并授权成员。连接检查不会自行安装 Webhook。

在 GitLab 项目中设置 **Merge request events**，目标为 `https://review.example.com/api/webhooks/gitlab/`，Secret 与平台配置一致。地址必须能从 GitLab 访问。当前支持 MR 创建、更新及相关 push 事件入队；平台中的 review / improve 操作也会创建同一队列任务。

API Token 使用部署者选择的专用 GitLab 服务账号，按其需要授予读仓库、发布评论及可选合并权限。是否能合并还受 GitLab 保护分支、审批和 CI 约束。

配置解析顺序：

- GitLab 地址：项目配置 → 系统设置 → `GITLAB_URL`。
- GitLab API Token：系统设置 → `GITLAB_TOKEN`。
- Webhook Secret：项目配置 → 系统设置 → `GITLAB_WEBHOOK_SECRET`。

全局 Token 只用于与全局 GitLab 地址同源的项目。携带 Token 的请求不跟随重定向。秘密字段只写入服务器；读取返回掩码与 configured 状态，保存时留空保留原值。

## 模型与网关

模型与引擎页面配置 OpenAI / Anthropic 兼容服务或 CCSwitch 网关。填写 Base URL、Key，测试获取真实模型 ID，再保存。项目可以独立覆盖模型、引擎和通知配置，三项继承开关互不影响。环境变量仅用于首次启动和未保存配置时的兜底。

CCSwitch 桌面程序需要另外开启代理，并保证对应上游账号已登录。模型列表可访问但为空、出现上游认证错误时，先检查 CCSwitch 上游会话。容器连接主机网关可用 `host.docker.internal`；主机网关需允许来自容器的连接。

调用模型会发送评审所需 diff 和上下文；选择符合团队数据要求的服务。模型输出不会直接发布：平台先核对提交、文件、新旧行号与原代码，再发布行级评论和总评。

## 可选评审引擎

默认镜像使用内置引擎。可选适配器需要安装运行时，并在模型与引擎页面完成版本探测和当前模型的合成验证后选择。CLI 仅接收当前模型认证信息，平台统一发布 GitLab 评论和企微通知。

已有非容器 Python/Node 环境可安装：

```bash
npm install -g @alibaba-group/open-code-review@1.12.9 @earendil-works/pi-coding-agent@0.86.0
python -m venv backend/.runtime/pr-agent
backend/.runtime/pr-agent/bin/python -m pip install -r backend/requirements-pr-agent.txt
```

Windows 使用 `backend\.runtime\pr-agent\Scripts\python.exe`。PR-Agent 必须使用独立环境，不装入 Django 环境；可通过 `REVIEW_PR_AGENT_PYTHON` 指定解释器。

容器可用下列派生 Dockerfile 安装运行时，版本由你固定和验证；第三方依赖遵循各自许可证：

```dockerfile
FROM node:22-bookworm-slim AS node
FROM ghcr.io/bennie-asi/cw-cr:latest
USER root
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g @alibaba-group/open-code-review@1.12.9 @earendil-works/pi-coding-agent@0.86.0 \
    && python -m venv /opt/pr-agent \
    && /opt/pr-agent/bin/pip install pr-agent==0.46.0
ENV REVIEW_PR_AGENT_PYTHON=/opt/pr-agent/bin/python
USER app
```

派生镜像不会因安装完成自动切换引擎。当前自动化验收覆盖默认内置引擎部署；第三方 CLI 的实际模型推理需在你的运行环境验证。

## 评审、建议和合并

行级定位根据实际 diff 计算，支持新增、删除、上下文和重命名。可信替换建议会生成 GitLab 原生 suggestion，作者或有权限成员可在 GitLab 手动应用；不可靠的替换会降级为说明。

评审历史分别记录生成、发布和通知阶段。失败任务按重试上限与退避恢复，完整快照用于避免重复评论。总评可从详情页编辑并重新同步。

自动合并默认关闭，需要项目管理员完成 dry-run 并显式启用。阻断阈值以及未评级问题均在服务端检查；合并状态不确定时先向 GitLab 对账，避免盲目重复请求。

## 企业微信

在通知配置中保存群机器人 Webhook，发送测试消息。可以设置平台默认或独立项目通知；评审与合并结果会记录业务返回码。状态 `unknown` 表示投递结果不确定，先核对群消息再决定是否手动重发。通知失败和代码评审失败分别记录。
