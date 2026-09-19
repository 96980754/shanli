# 生产部署指南

本文档介绍如何在生产环境中部署 AI知识库。

## 前置要求

- Docker Engine (v24.0+)
- Docker Compose (v2.20+)
- NVIDIA Container Toolkit（如需使用 GPU 服务）

::: warning 注意事项
1. 生产环境和开发环境建议使用不同的机器，避免端口和资源冲突
2. 虽然名为「生产环境」，但这只是基本配置，真正上线需要根据实际情况调整
3. 前端有调试面板（长按侧边栏触发），生产环境建议关闭
:::

## 部署步骤

### 1. 准备配置文件

为避免与开发环境冲突，生产环境使用独立的 `.env.prod` 文件：

```bash
cp .env.template .env.prod
```

`docker-compose.prod.yml` 通过各服务的 `env_file` 读取 `.env.prod`，但 compose 的 `${VAR}` 插值**不读 `env_file`**——它只读项目目录下的 `.env` 或 `--env-file` 指定的文件。因此启动命令必须带上 `--env-file .env.prod`（见下一步），否则以插值取值的服务（Milvus 的 MinIO 凭据、Milvus/Neo4j 的连接参数等）会拿到空串或默认值。

**以下变量必须手工填写**，其中前四项留空会造成「服务正常启动、健康检查通过，但系统用不了」：

| 变量 | 留空的后果 | 取值方式 |
|------|-----------|----------|
| `JWT_SECRET_KEY` | 所有登录与令牌校验抛 `ValueError`，**没人能进入系统** | `openssl rand -hex 32` |
| `YUXI_INSTANCE_ID` | 同上 | `openssl rand -hex 8` |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | MinIO 退到默认 `minioadmin`、Milvus 拿到空串，两边凭据不一致，向量库无法访问对象存储 | 强随机值 |
| `NEO4J_PASSWORD` | 沿用 compose 默认弱密码 | 强随机值 |
| `SILICONFLOW_API_KEY` 等模型密钥 | 无法调用模型，问答不可用 | 至少配置一个 provider |
| `YUXI_CORS_ORIGINS` | 跨域部署时浏览器请求被拒绝；同源部署可留空 | 见「跨域（CORS）配置」 |
| `PADDLEOCR_API_TOKEN` | 仅当把 `default_ocr_engine` 切到 `paddleocr_vl_1_6` 时需要；代码默认 `rapid_ocr`，本地无需 token | 见「文档解析」 |
| `WECOM_TOKEN` / `WECOM_ENCODING_AES_KEY` / `WECOM_CORP_ID` | 企业微信回调与客服接管不可用 | 需要该功能时填写 |
| `UDESK_SUBDOMAIN` / `UDESK_EMAIL` / `UDESK_OPEN_API_TOKEN` | 客服记录回流不可用 | 三项均可在设置页「Udesk 对接」自助填写，保存即生效；环境变量仅作初始默认值。token 是永久凭证，属**只写字段**——保存后落服务器配置文件与 Redis 快照，但任何读取接口都不回传明文，页面只显示是否已配置 |

::: warning 初始账号
正常路径是**首次打开网页时的「初始化管理员」引导**：`GET /api/auth/check-first-run` 在 `users` 表为空时返回 `first_run=true`，前端据此显示设置页，由部署者自行设定账号与密码（`POST /api/auth/initialize`，非空库返回 403）。这条路径不需要任何环境变量。

`make seed`（`backend/scripts/seed_initial_users.py`）是**开发/自动化场景的替代路径**，在空库上批量建号：它的账号与密码全部取自 `YUXI_SUPER_ADMIN_NAME` / `YUXI_SUPER_ADMIN_UID` / `YUXI_SUPER_ADMIN_PASSWORD`，缺任一项即报错退出，不使用任何内置凭据，也不会把密码打印到输出。注意二者只能选其一——执行过 `make seed` 后库非空，网页端的初始化引导会被拒绝。

示例部门与人员（3 个部门 + 6 个部门管理员 + 14 个普通用户）已改为**显式开启**：只有 `make seed-demo` 才会创建，其共用密码取自 `YUXI_DEMO_USER_PASSWORD`，生产环境不要设置。此前该脚本内置了固定的超级管理员凭据（含真实姓名与手机号）并会把密码打印到标准输出，任何拿到源码的人都能据此登录空库部署的超级管理员——该内置凭据已移除。
:::

### 2. 启动服务

使用生产环境配置文件启动：

```bash
# 仅启动核心服务（CPU 模式）
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build

# 启动所有服务（包含 GPU OCR）
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile all up -d --build
```

### 3. 验证部署

- Web 访问：http://localhost（直接通过 80 端口）
- API 健康检查：`curl http://localhost/api/system/health`

## 跨域（CORS）配置

`docker-compose.prod.yml` 默认把 `YUXI_ENV` 设为 `production`，后端在该环境下会按 `YUXI_CORS_ORIGINS` 显式声明允许的来源。**未配置时返回空列表，浏览器跨域请求会被拒绝**。生产部署前请根据前端与 API 的相对位置选择策略：

| 部署形态 | 推荐配置 |
|----------|----------|
| 前端与 API 同源（Nginx 同端口反代） | 不需要设置，留空即可 |
| 前端与 API 跨域部署 | `YUXI_CORS_ORIGINS=https://your-frontend.example.com` |
| 多个前端域名 | 逗号分隔，如 `https://a.example.com,https://b.example.com` |
| 完全放开（不推荐） | `YUXI_CORS_ORIGINS=*`，会自动关闭 credentials，登录态/JWT 无法跨域携带 |

开发环境（`YUXI_ENV=development` 且未设置该变量）默认允许 `http://localhost:5173` 与 `http://127.0.0.1:5173`，方便本地前后端独立启动调试。从 0.7.0 升级到 0.7.1 时，如果此前是跨域部署但未显式声明来源，必须补上 `YUXI_CORS_ORIGINS`，否则前端跨域请求会被拒绝。

## 维护与更新

### 更新代码

```bash
# 拉取最新代码
git pull

# 重新构建并启动
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

### 查看日志

```bash
# API 日志
docker logs -f api-prod

# Nginx 访问日志
docker logs -f web-prod
```
