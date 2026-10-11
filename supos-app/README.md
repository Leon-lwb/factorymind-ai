# FactoryMind AI — open supOS 标准应用包

本目录是 FactoryMind AI 的 **open supOS 标准应用形态（形态 A）** 交付包，结构与官方样板
`supos-ce-deploy/mount/backend/apps/spl-pl-distribution/` 对齐，可通过应用管理
（`/inter-api/supos/app/uploadApp` + `/app/install`）安装。

## 目录结构

```
supos-app/
├── app.yaml          # 应用清单（name/vendorName/indexUrl/deployMode…）
├── build.yaml        # 镜像构建描述（images[].dockerPath/dockerfile）
├── compose.yaml      # 容器编排描述（web + engine 两容器）
├── vendor.yaml       # 厂商信息
├── config.ini        # 部署期配置（server/supos/ai/MQTT）
├── open_api.yaml     # 对外暴露的 OpenAPI
├── dependency.yaml / dependencies.yaml / requirement.yaml
├── data/             # 应用元数据：module / menu / i18n / syscode
├── statics/          # 静态资源（favicon）
├── bin/backend/      # 引擎镜像（Dockerfile；源码打包时从 services/ai-engine 暂存）
├── bin/front/        # 前端镜像（Dockerfile + nginx.conf；打包时暂存工作区）
└── scripts/package-app.ps1   # 组装并打出可安装的 zip
```

> `bin/` 在仓库里只提交 Dockerfile；源码由 `scripts/package-app.ps1` 在打包时从
> 仓库暂存，避免同一份源码出现两个副本（唯一事实来源仍是 `services/ai-engine` 与 `apps/web`）。

## 命名与路由约定

| 字段 | 值 | 说明 |
|---|---|---|
| `name` | `quality-copilot` | 应用标识 |
| `vendorName` | `factorymind` | 厂商标识 |
| `indexUrl` | `/apps/factorymind/#/overview` | 网关路由前缀 = CE 默认 `/apps/<vendorName>/`（真机实测，见 §8-3） |
| `deployMode` | `INSIDE` | 与样板一致；真机实测：CE 安装器**不做容器编排**，编排由上层/运维执行（见 §8-2） |

前端使用 **HashRouter** 且 Vite `base` 为相对路径（`./`）；**API 基址 `BASE_URL` 也必须是相对
`./api`**（见 `apps/web/src/api/client.ts`）——这样 `./api` 在 `/apps/factorymind/` 下解析为
`/apps/factorymind/api/...`，网关剥前缀后落到 web 容器的 `/api/...`。若写成绝对 `/api`，请求会
打到网关根 `/api/`（被 chat2db 占用）。前端因此可在任意 `/apps/<prefix>/` 下运行，网关无需子路径重写。

容器 DNS：supOS 以 `<appName>-<containerName>` 寻址容器，故包内前端 nginx 反代到
`quality-copilot-engine:8100`（应用 `quality-copilot` + 容器 `engine`）。此约定由样板印证
（`spl-pl-distribution` 反代 `agv-monitor-backend:8080`）。
⚠️ 真机部署时，本机网关**不**用容器名做 upstream（避免 upstream 解析失败中断网关），而是
经 `host.docker.internal:8080` 指向正在运行的 web 容器；完整路由块见
`docs/SUPOS_INTEGRATION_CHECKLIST.md` §8"应用包安装与网关路由"。

## 构建镜像

```powershell
# 组装 + 打 zip（不构建镜像）
powershell -File scripts/package-app.ps1

# 组装 + 打 zip + 构建两个镜像
powershell -File scripts/package-app.ps1 -BuildImages
```

产物：`quality-copilot-app.zip`，以及（加 `-BuildImages` 时）镜像
`factorymind-ai-front:1.0.0` 与 `factorymind-ai-backend:1.0.0`。

## 安装到 open supOS

> ⚠️ `/inter-api/supos/*` 的鉴权载体是 **Cookie `supos_community_token=<keycloak access_token>`**，
> `Authorization: Bearer` 会被网关拦成 401（真机实测）。

```bash
# 取令牌（Keycloak password 授权，username/password = supos / supos）
TOKEN=$(curl -s -X POST "http://<host>:18088/keycloak/home/auth/realms/supos/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=supos&client_secret=<client_secret>&username=supos&password=supos" \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 上传应用包（先传后装）
curl -F "file=@quality-copilot-app.zip" -F "autoInstall=false" \
     -H "Cookie: supos_community_token=$TOKEN" \
     http://<host>:18088/inter-api/supos/app/uploadApp

# 安装
curl -X POST -H "Cookie: supos_community_token=$TOKEN" \
     -H "Content-Type: application/json" -d '{"name":"quality-copilot"}' \
     http://<host>:18088/inter-api/supos/app/install
```

**安装后还需一步（CE 安装器不做容器编排，见 §8-2）**：在运维侧把应用路由写入
`${VOLUMES_PATH}/nginx/routes-app.conf` 并 reload nginx。路由块与验收证据见
`docs/SUPOS_INTEGRATION_CHECKLIST.md` §8"应用包安装与网关路由"。

安装后入口：`http://<host>:18088/apps/factorymind/`

## 实例核验结论（2026-10-11 真机，open supOS CE `V1.5.0.0-M`）

| 原待确认项 | 实测结论 |
|---|---|
| `compose.yaml`/`build.yaml` 的容器编排由谁执行 | CE `InstallApp` **不 build、不 up 任何容器**；只复制前端资源 + i18n + 登记路由。编排由上层/运维执行（§8-2）。 |
| 路由前缀形态 | CE 默认 = **`/apps/<vendorName>/`** ⇒ `/apps/factorymind/`（§8-3）。已据此统一 `app.yaml`、`data/menu.yaml`。 |
| 容器 DNS `<appName>-<containerName>` | 样板印证为 `agv-monitor-backend:8080`；本机实际部署改用 `host.docker.internal:8080` 直连运行中的 web 容器（见上）。 |
| 路由注册（形态 B）是否生效 | 后端写入 `plugins-frontend/.nginx/app-routes/*.conf`，但 nginx 仅 `include /etc/nginx/*.conf`（不含子目录）⇒ **本构建中形态 B 不生效**；真正生效点是 `${VOLUMES_PATH}/nginx/routes-app.conf`。 |
| `data/menu.yaml` 是否形成门户菜单 | **否**。CE 后端源码不引用 `menu/module/syscode.yaml`，OpenAPI 无菜单接口（`installStepFlags` 仅 front/i18n/route）⇒ 门户侧边栏无本应用入口（实测确认）。演示/接入直接访问 `/apps/factorymind/`；菜单集成属上层应用商店职责。 |
