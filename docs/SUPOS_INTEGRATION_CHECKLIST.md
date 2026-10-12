# open supOS 集成对接清单

> FactoryMind AI 项目 · 基于 Gitee 源码的集成核验
> 核验日期：2026-10-09（v1.2 更新：2026-10-11 加入**真机实测**结论；v1.3 更新：2026-10-12 加入 **Provider 真机取数闭环**）
> 核验方式：**直接读取 Gitee 源码**（本地克隆 `supos-ce-backend` @ `b0930be`、`supos-ce-deploy` @ `a36787c`）+ **本地 supOS CE 实例实测**（网关 `:18088`），非文档猜测
> 状态：v1.3（锁定集成面 + 大赛提交要求与赛程 + 真机拉起/登录/鉴权实测 + **Provider 真机端到端取数验收**；带 ⚠️ 或 ⏳ 的项需在部署实例上最终确认）

本清单取代 `docs/SUPOS_RESEARCH.md` 中的"候选/待验证"表述——凡本文写明的端点，均来自仓库源码或按源码生成的 `INTER_API.md`。

---

## 0. 结论摘要（TL;DR）

1. **集成基线应升级到 `V1.5.0.0-M`**（原锁定的 `V1.3.0.0-M` 已落后两个版本）。
2. **数据读取端点已可确定**，现有 `SupOsEndpoints` 中 `nodes` 与 `events` 两条路径**写错了**，须修正（见 §7）。
3. **"标准应用形态"有官方样板**：`supos-ce-deploy/mount/backend/apps/spl-pl-distribution/` 就是一个真实交付包，其 `app.yaml / build.yaml / compose.yaml / vendor.yaml` 结构即命题所指的"可通过应用管理安装的标准应用形态"（见 §6）。
4. **命题方向一要求"真实 open supOS 环境运行 + 3~5 分钟真实环境录屏"**，因此必须起一套本地 supOS CE（`supos-ce-deploy`，WSL2），不能只跑 Mock。

---

## 1. 集成基线版本

| 项 | 值 | 来源 |
|---|---|---|
| 最新 Release tag | **`V1.5.0.0-M`** | `git ls-remote --tags supos-ce-deploy` |
| 历史 tag | `V1.3.0.0-M`、`V1.4.0.0-M-WIN`、`V1.5.0.0-M` | 同上 |
| master `.env` | `OS_VERSION=V1.5.0.0-M` | `supos-ce-deploy/.env` |
| backend master 最新提交 | `b0930be`（2026-09-30，feat: support mosquitto mqtt broker selection） | `git log` |

**决策**：集成与答辩材料统一以 **`V1.5.0.0-M`** 为基线；文档/PPT 中显式注明版本号，避免 API 漂移争议。

---

## 2. 标准运行环境（命题硬性要求的"标准环境"）

| 项 | 要求 | 来源 |
|---|---|---|
| OS | Ubuntu Server 24.04（Windows 仅 WSL2） | `supos-ce-deploy/README-CN.md` §1.1 |
| Docker | Engine 27.4.0 / buildx 0.19.2 / Compose v2.31.0 | 同上 |
| Compose 规格 | `docker-compose-4c8g.yml` 或 `docker-compose-8c16g.yml`（服务集合相同，资源不同） | `README-CN.md` §核心组件 |
| 核心服务（9） | `backend`、`nginx(OpenResty)`、`nodered`、`emqx`、`postgresql`、`tsdb`、`keycloak`、`portainer`、`chat2db` | 同上 |
| 可选 profile | `grafana`、`minio`、`pi-agent` | 同上 |
| 安装 | `bash bin/install.sh`（支持 `--non-interactive`）；安装前 `bash bin/doctor.sh` | `README-CN.md` §安装指南 |
| 持久化 | `VOLUMES_PATH` 下分目录；`backend/apps`、`backend/third-apps`、`plugins/package` 等 | `docs/compose-service-map.md` §Persisted state |

**端口占用（我们的容器必须避开）**：网关 `8088`/`8443`、EMQX `1883`/`8083`/`8084`/`18083`、PostgreSQL `5432`、TSDB `2345`、Keycloak `8081`、Node-RED `1880`、Portainer `8000`/`9443`、（可选）Grafana `3000`、MinIO `9000`/`9001`。

---

## 3. 网关与认证

**网关**：`nginx` 运行 **OpenResty**，监听 `:8088`（https `:8443`）。三类路由前缀：

- `/inter-api/supos/`（登录态）
- `/service-api/supos/`
- `/open-api/`（外部）

Swagger / OpenAPI：`http://<host>:8088/swagger-ui`、`/v3/api-docs`、`/inter-api/supos/supOS-CE.openapi.yaml`。

**认证**（Keycloak 26，OAuth2 授权码流程）：

| 端点 | 方法 | 说明 |
|---|---|---|
| `/inter-api/supos/auth/code` | GET | 原样返回授权码，供登录回调 |
| `/inter-api/supos/auth/token` | GET | 用 `code` 换 token，写入登录 cookie |
| `/inter-api/supos/auth/userinfo` | GET | 当前登录用户；无有效信息返回 `auth.unauthorized` |
| `/inter-api/supos/auth/user` | GET | 未启用认证时返回 `guest` |
| `/inter-api/supos/auth/logout` | DELETE | 退出登录 |

来源：`supos-ce-backend/INTER_API.md` §基础与认证、`internal/interfaces/http/auth_routes.go`。

> **【真机实测 2026-10-11】登录态与鉴权载体（重要）**
> 网关对 `/inter-api/supos/*` 的守卫由 `nginx` 的 lua `router_lib.lua::get_supos_token()` 实现，**只读名为 `supos_community_token` 的 Cookie**，**不读 `Authorization` 头**：
> - `Authorization: Bearer <token>` → **401 Authorization Required**（openresty 直接拦截）
> - `Cookie: supos_community_token=<token>` → **200**
> Cookie 的值就是 Keycloak 的原始 `access_token`。因此任何访问 `/inter-api/supos/*` 的客户端（含本项目 `HttpSupOsClient` 与 `seed_uns.py`/`verify_supos.py`/`register_supos_route.py`）**必须用 Cookie，而非 Bearer 头**。
>
> 另：`/open-api/*` 走另一套鉴权——读 `apikey` 头（或 `apikey` 查询参数），值为 `.env` 的 `SUPOS_API_KEY`（见 §8-1）。

---

## 4. UNS 建模与数据读取（核心集成面）

来源：`supos-ce-backend/INTER_API.md` §UNS、`internal/interfaces/http/router.go`。

| 用途 | 端点 | 方法 | 关键参数 |
|---|---|---|---|
| 创建文件夹/文件模型 | `/inter-api/supos/uns/model` | POST | `pathType`(0 文件夹/2 文件)、`name`、`alias`、`parentAlias`/`parentId`、`dataType`、`fields/definition`、`persistence`、`addFlow`、`addDashBoard`、`expression`、`frequency`、`templateAlias`、`labelNames` |
| 批量创建 UNS 节点 | `/inter-api/supos/uns/batch` | POST | Body: `[]BatchCreateTopicRequest` |
| 更新模型 | `/inter-api/supos/uns/model` | PUT | `id` 或 `alias`（至少一个） |
| 模型订阅（→ 实时） | `/inter-api/supos/uns/model/subscribe` | PUT | `id`、`enable`、`frequency`、`topic` |
| **查询 UNS 树** | `/inter-api/supos/uns/tree` | GET | `parentId`、`pathType`、`key`、`type` |
| 条件查询 UNS 树 | `/inter-api/supos/uns/condition/tree` | POST | `pageNo`、`pageSize`、`searchType`、`keyword`、`parentId`、`dataType`、`pathType` |
| 模型详情 | `/inter-api/supos/uns/model` | GET | `id` |
| 实例详情 | `/inter-api/supos/uns/instance` | GET | `id` |
| 分页搜索文件 | `/inter-api/supos/uns/search` | GET | `k`、`type`、`dataTypes`、`pageNo`、`pageSize` |
| **当前值批量查** | `/inter-api/supos/uns/file/current/batchQuery` | POST | Body: `[]alias` |
| 当前值批量写 | `/inter-api/supos/uns/file/current/batchUpdate` | POST | Body: `[{alias, data}]` |
| 最新值 | `/inter-api/supos/uns/getLastMsg` | GET | `id`/`alias`/`path`（至少一个） |
| **历史时序查询** | `/inter-api/supos/uns/file/history/timeseries/query` | POST | `alias`、`startTime`、`endTime`、`field/fields`、`aggregation`、`groupBy`、`pageNo`、`pageSize`、`order` |
| 历史关系查询 | `/inter-api/supos/uns/file/history/relation/query` | POST | `alias`、`startTime`、`endTime`、`fields`、分页 |
| 历史批量查询 | `/inter-api/supos/uns/file/history/batch/query` | POST | `select`、`where`、`groupBy`、`fill`、`limit`、`offset`、`order` |
| UNS 实时 WebSocket | `/inter-api/supos/uns/ws` | GET(WS) | `id`、`topic`、`file`、`heartbeatMillis` |

**`dataType` 枚举**：`1` 时序、`2` 关系、`3` 计算、`6` 聚合/合并、`7` 引用、`8` JSONB。
**`pathType` 枚举**：`0` 文件夹、`2` 文件。

> 建模落点：FactoryMind 的 `Factory→Line→Station→Equipment→Process` 用 `pathType=0` 建层级文件夹；质量指标（FPY/NOK/扭矩/节拍/工具故障率）用 `pathType=2` + `dataType=1`（时序）建文件节点。

---

## 5. 数据接入（OT / 外部数据）

| 用途 | 端点 | 方法 | 关键参数 |
|---|---|---|---|
| 外部 MQTT topic 树 | `/inter-api/supos/external/tree` | GET | `key` |
| 解析 payload → 字段定义 | `/inter-api/supos/external/parserTopicPayload` | GET | `topic` |
| **外部 topic 建为 UNS 文件** | `/inter-api/supos/external/topic2Uns` | POST | Body: `CreateFileCommand`（见下） |
| 基于 UNS 建 Node-RED 流程 | `/inter-api/supos/flow/create` | POST | `unsAlias`、`path` |
| 部署流程 | `/inter-api/supos/flow/deploy` | POST | `id`、`flows`(JSON) |
| 按 alias 查关联流程 | `/inter-api/supos/flow/uns/alias` | GET | `alias` |

**`CreateFileCommand` 字段**（`internal/domain/uns/types.go`）：
`name`、`alias`、`displayName`、`path`、`parentAlias`、`dataType`、`description`、`definition`、`persistence`、`dashBoard`、`addFlow`、`accessLevel`、`refers`、`expression`、`frequency`、`extendProperties`、`templateAlias`、`labelNames`、`labelList`、`parentDataType`。

**后端自身 MQTT 订阅**（`internal/infrastructure/mqtt/notifier.go`）：
环境变量 `SUPOS_BACKEND_MQTT_ENABLED`、`SUPOS_BACKEND_MQTT_BROKER`、`SUPOS_BACKEND_MQTT_TOPIC_PREFIX`；连接 broker 后订阅 `#`，再按前缀过滤自己发布的 current-value 主题。

**参考 App 的 MQTT 用法**（`spl-pl-distribution/config.ini`）：`MQTT.BROKER=emqx:1883`，`MQTT.TOPIC=<厂名>/<产线>/订阅`。

---

## 6. 应用集成（两种形态）

### 形态 A：App 交付包（命题推荐 → "可通过应用管理安装的标准应用形态"）

**官方样板**：`supos-ce-deploy/mount/backend/apps/spl-pl-distribution/`（一个真实交付包，`name: agv-monitor`）。顶层结构：

```
<app-root>/
├── app.yaml          # 应用清单（见下）
├── build.yaml        # 镜像构建描述
├── compose.yaml      # 容器编排描述
├── vendor.yaml       # 厂商信息
├── config.ini        # 应用配置（server/supos/MQTT）
├── open_api.yaml     # 对外 OpenAPI 暴露
├── dependencies.yaml / dependency.yaml / requirement.yaml
├── bin/front/        # 前端（React+TS+Vite + Dockerfile + nginx.conf）
├── bin/backend/      # 后端 + Dockerfile
├── data/  statics/   # 应用元数据与静态资源
├── scripts/          # 构建 + 镜像导出脚本
└── images/           # 导出的镜像 tar
```

**`app.yaml` 字段**（实测样板）：
```yaml
apiVersion: v4alpha1
name: agv-monitor
vendorName: guomeng
appVersion: V1.0.0
showName: 嘉兴山蒲照明电器
description: ...
doc:
icon: /statics/favicon.svg
indexUrl: /apps/guomeng-agv-monitor/#led-line-monitor
type: normal
deployMode: INSIDE
```

**`build.yaml`**：`images: [{name, tag, dockerPath, dockerfile}]`
**`compose.yaml`**：`service.multiReplicas/replicas`、`proxy.paths[{path, servicePort}]`、`ports[]`、`containers[{name, image, resources, ports, entrance: route}]`
**`vendor.yaml`**：`vendor.bluetronId/showName/organization/url/email/copyright/address`

**安装 API**：

| 端点 | 方法 | 参数 |
|---|---|---|
| `/inter-api/supos/app/uploadApp` | POST | Form: `file`（应用包）+ `autoInstall` |
| `/inter-api/supos/app/install` | POST | Body: `name` |
| `/inter-api/supos/app/uninstall` | DELETE | Query: `name` |
| `/inter-api/supos/app` | GET | 应用列表 |
| `/inter-api/supos/app/{name}` | POST | 应用详情 |

> 后端 `InstallApp` 实测行为（`internal/application/plugin/service.go`）：把应用包解压 → 复制静态资源到 `/data/apps/<name>` → 复制 i18n → 注册路由。**应用包内 `compose.yaml`/`build.yaml` 的容器编排由谁执行，源码未在 CE backend 内体现** ⚠️，需在部署实例确认（可能由应用商店/上层机制负责）。

### 形态 B：路由注册（外部容器，本项目当前路径）

**`AppRouteRegistration` 结构体**（`internal/application/plugin/service.go:252`）：

```go
type AppRouteRegistration struct {
    AppID      string `json:"appId"`                // 必填，安全 token：[A-Za-z0-9_.-]
    VendorName string `json:"vendorName"`           // 必填，安全 token
    Prefix     string `json:"prefix,omitempty"`     // 可选，默认 "/apps/<vendorName>/"
    Target     string `json:"target"`               // 必填，http(s)://host:port，不含 path/query
}
```

- `POST /inter-api/supos/app/routes/register`（Body 上述结构）→ 生成 nginx `location ^~ <prefix> { proxy_pass <target>; }` 并 reload。
- `POST /inter-api/supos/app/routes/unregister`（Body: `appId`）。
- **校验规则**：`prefix` 必须以 `/apps/` 开头、`/` 结尾，且不含 `//`、`..`；`target` 必须是 `http(s)://host:port`。

---

## 7. 对现有代码的修正（`services/ai-engine/app/providers/supos.py`）

现有 `SupOsEndpoints` 三条路径中**两条需修正**：

| 字段 | 现值 | 核验结论 | 修正为 |
|---|---|---|---|
| `nodes` | `/inter-api/supos/uns/model/nodes` | ❌ 源码无此路径；且 `GET /uns/tree` 在本构建受 `LAZY_TREE=true` 限制**只回 10 行且忽略 `pageSize`**（见 §8"Provider 真机取数"块） | **`POST /inter-api/supos/uns/condition/tree`**，body `{"pageNo":1,"pageSize":500,"searchType":1}` |
| `timeseries` | `/inter-api/supos/uns/file/history/timeseries/query` | ✅ 正确 | 保持不变 |
| `events` | `/inter-api/supos/uns/event/query` | ❌ 源码无此路径 | 事件来自：① MQTT 订阅 ② `GET /inter-api/supos/uns/getLastMsg` ③ `WS /inter-api/supos/uns/ws` ④ 历史时序查询 |

**建议补充**：`current_values: POST /inter-api/supos/uns/file/current/batchQuery`（批量取当前值，比历史查询更贴合"实时快照"）。

---

## 8. 待部署实例实测项（源码无法替代）

> **【真机实测 2026-10-11】** 已有实例在跑（Docker Desktop / WSL2，网关 `http://localhost:18088`，`ENTRANCE_DOMAIN=192.168.1.65`）。下列前两项已闭环，其余仍待确认。

| # | 待确认项 | 影响 | 实测结论（2026-10-11） |
|---|---|---|---|
| 1 | `/open-api/` + `SUPOS_API_KEY` 是否真正实现 | 决定外部免登录集成是否可行 | ✅ **已实现**。`GET /open-api/health` → 200（公开，无需 key）；`GET /open-api/` 无 key → 401，带 `apikey: <SUPOS_API_KEY>` → 通过鉴权。⚠️ `/open-api/restapi/*`、`/open-api/graphql/*` 反代到 `hasura:8080`，**CE 默认栈未部署 Hasura → 502**；故 UNS 数据仍走 `/inter-api` + Cookie。 |
| 2 | 应用包 `deployMode: INSIDE` 的容器编排由谁执行 | 决定"标准应用形态"如何落地 | ✅ **已实测（10/11）**：CE 后端 `InstallApp` 仅做 ①复制包内前端资源 ②i18n ③`saveAppRoute` 路由登记；**不解析/不执行 `compose.yaml`/`build.yaml`，不 build、不 `up` 任何容器**。安装后 `/data/apps/quality-copilot/` 只有清单 + `data/` + `statics/`（**无 `index.html`、无 `bin/`**）；`installStepFlags:["front","i18n","route"]` 即三段实际动作。⇒ INSIDE 的容器编排由**上层应用商店/运维**执行（本例：FactoryMind 的 web + engine 容器已在 supOS 之外运行，网关再路由到它）。 |
| 3 | 路由前缀实际形态：`/apps/<vendor>/` vs `/apps/<vendor>-<name>/` | 影响 `indexUrl` 与注册参数 | ✅ **已实测（10/11）**：CE 后端默认前缀 = **`/apps/<vendorName>/`**（`service.go` `normalizeAppRouteRegistration`，`prefix` 缺省时取此值）⇒ `vendorName: factorymind` → **`/apps/factorymind/`**。样板的 `/apps/guomeng-agv-monitor/` 是上层自定义的 `<vendor>-<name>` 形式。已据此把 `app.yaml` `indexUrl` 与 `data/menu.yaml` 全量改为 `/apps/factorymind/`。 |
| 4 | `topic2Uns` 的实际 MQTT 桥接 topic/载荷规则 | 影响事件接入实现 | ✅ **已实测闭环**（见下方"MQTT 事件通路"块）：topic = UNS 节点 **path**（`USE_ALIAS_PATH_AS_TOPIC=false`），载荷 `{"value":..}` 直写节点当前值 |
| 5 | `V1.3` 与 `V1.5` 之间的 API 漂移 | 影响基线选择 | ⏳ 待验；本项目已按 `V1.5.0.0-M` 锁定 |
| 6 | 本机 WSL2 + Docker 资源是否满足 8C16G | 决定能否本地真实环境录屏 | ✅ **可跑通**。以 Docker Desktop（Linux 引擎，`docker-desktop` 发行版）+ `docker-compose-4c8g.yml` 起全套；9 核心服务 + grafana 均 `Up`，`postgresql`/`tsdb`/`nodered` healthy。 |

**真机附加结论**：
- **鉴权载体**：`/inter-api/supos/*` 必须用 `supos_community_token` Cookie（见 §3 实测块），Bearer 头无效。
- **登录核验**：默认账号 `supos` / `supos` 可用；Keycloak `supos` client 允许 `password` 授权，直接换取 `access_token`（含 `super-admin` 角色）。
- **网关可达性**：`GET /` 与 `/home` → 302 跳 `/supos-login`；`/inter-api/supos/uns/tree` 带 Cookie → `{"code":0,"data":[],"msg":"ok"}`（当前 UNS 为空）；`/auth/userinfo` 带 Cookie → `superAdmin: true, homePage: "/home"`。
- **浏览器登录入口（重要）**：**必须用 LAN IP `http://192.168.1.65:18088/`，不可用 localhost/127.0.0.1**。`login_access.lua` 按请求 `Host` 动态拼 OAuth `redirect_uri`，而 Keycloak `supos` client 只白名单了该 IP。若报"**无效的参数 : redirect_uri**"，根因是改 `ENTRANCE_PORT`(8088→18088) 后**未执行平台自带的 `supos-ce-deploy/bin/util/changeIp.sh`**（它直接改 Keycloak 的 PG：`client`/`redirect_uris`，client UUID `a7b53e5e-3567-470a-9da1-94cc0c7f18e6`）。手工等效修复：`UPDATE client SET base_url/root_url='http://192.168.1.65:18088[/home]'` + `INSERT INTO redirect_uris (...) VALUES (...,'http://192.168.1.65:18088/inter-api/supos/auth/token')`，再 `docker restart keycloak`。修复后 `GET /supos-login` → 200 登录表单；`supos`/`supos` 登录成功。
- **`/swagger-ui`、`/v3/api-docs`**：反代 location 存在，但此构建返回 **404**（Swagger 未启用）；`/inter-api/supos/supOS-CE.openapi.yaml` 需登录（无 Cookie 时 401），故 §8 复核以该 yaml + 源码为准。
- **起栈命令**（install.sh 实际调用，供复现）：
  `docker compose --env-file .env --env-file .env.tmp --project-name supos --profile grafana -f docker-compose-4c8g.yml up -d`
  ⚠️ 必须带 `--project-name supos`，否则 Compose 想新建 `supos-ce-deploy_default_network` 会与已存在的 `supos_default_network`（172.31.254.0/24）**子网冲突**。

### MQTT 事件通路（实测闭环 2026-10-11）

**Broker**：EMQX `5.8.4`，`tcp://localhost:1883`（本机 Windows 侧 `0.0.0.0:1883->1883`；容器内 `emqx:1883`）。匿名连接可用（`connect rc=0`）。

**订阅侧（后端）**：`internal/infrastructure/mqtt/notifier.go` 的 `CurrentValueSubscriber` 订阅 **`#`**（全量），
并**忽略**自身发布前缀 `SUPOS_BACKEND_MQTT_TOPIC_PREFIX`（默认 `supos/current`）的消息，避免回环。

**topic → UNS 映射规则**（源码 + 实测）：
- `USE_ALIAS_PATH_AS_TOPIC=false`（本项目 `.env` 默认）⇒ **MQTT topic = UNS 节点的 `path`**；置 `true` 则用 `alias`。
- 消息 topic 命中已知节点 path → 调 `SubscribeCurrentValue` **直写该节点当前值**（带前导 `/` 也可）。
- topic 未命中任何节点 → 作为"外部主题"缓存，可从 `GET /inter-api/supos/external/tree` 看到，
  再用 `POST /inter-api/supos/external/topic2Uns`（body `CreateFileCommand`）映射到 UNS。

**实测证据**（paho-mqtt 发布 → REST 读回）：
```
publish topic = 示范总装工厂/总装一线/关键扭矩拧紧/关键扭矩拧紧_一次合格率_FPY
payload = {"value":98.7,"status":0}
GET /inter-api/supos/uns/getLastMsg?alias=ST_A40_FPY
  → {"code":0,"data":{"data":{"value":98.7},"payload":"{\"value\":98.7}","updateTime":1791728583911}}
```
即：**FactoryMind 侧只需向"节点 path"主题发一条 `{"value":..}`，supOS 节点当前值即更新**（无需登录态，仅 MQTT）。
未知主题（如 `/mqtt/device/temp`）则落入 external 缓存，实测 `external/tree` 正确回显层级。

> 注：节点 path 的段用的是**显示名（中文）**，例如 `ST_A40_FPY` 的 path 为
> `/示范总装工厂/总装一线/关键扭矩拧紧/关键扭矩拧紧_一次合格率_FPY`，而 alias 为 `ST_A40_FPY`。
> 生产接入建议置 `USE_ALIAS_PATH_AS_TOPIC=true` 以 alias 作 topic，避免中文/重名带来的主题脆弱性。

**方向性结论（重要）**：supOS 在 **MQTT ingress 时不会回发**消息——用 paho 订阅 `#` 期间向节点 path 发布，
只收到自己发的那条，`supos/current/#` 无任何消息。即：
- **写方向（本项目主用）**：FactoryMind 计算 KPI → 向"节点 path（或 alias）"主题发 `{"value":..}` → supOS 节点当前值更新。✅ 已验证。
- **读方向**：`supos/current/<alias>` 仅在 supOS 平台自身通过 UNS/发布 API 写值时发出；
  若 FactoryMind 需消费平台侧值，应订 `supos/current/#`（前缀可由 `SUPOS_BACKEND_MQTT_TOPIC_PREFIX` 改），
  而**不是**默认的 `factorymind/uns/#`（该值只用于 FactoryMind 内部约定，不会收到 supOS 平台消息）。

### 应用包安装与网关路由（真机闭环 2026-10-11）

**安装流程实测（Cookie 鉴权）**：
```
POST /inter-api/supos/app/uploadApp   -F file=@quality-copilot-app.zip -F autoInstall=false
  → {"code":0,"data":{"appInfoYml":{...},"installStatus":"notInstall",...}}
POST /inter-api/supos/app/install     -d '{"name":"quality-copilot"}'
  → {"code":0,"data":"quality-copilot"}
GET  /inter-api/supos/app
  → installStatus:"installed", installStepFlags:["front","i18n","route"],
    publicPath:"/data/apps/quality-copilot"
```
即：安装器把包内容搬到 `/data/apps/quality-copilot`（**仅清单 + `data/` + `statics/`**），登记 `route`，但**不产出可服务的 `index.html`**，也不起容器（见 §8-2）。

**路由集成点（本 CE 构建的关键事实）**：
- 后端 `RegisterAppRoute`（形态 B）把 nginx 片段写到 **`plugins-frontend/.nginx/app-routes/<appID>.conf`**，而 nginx 主配置仅 `include /etc/nginx/*.conf`（**不含子目录**）→ 形态 B 注册在本构建中**实际不生效**（本机 `routes-app.conf` 安装前后为空即证据）。
- 真正生效的共享集成点是 **`${VOLUMES_PATH}/nginx/routes-app.conf`**：
  后端容器内 = `/data/nginx/routes-app.conf`，nginx 容器内 = `/etc/nginx/routes-app.conf`，**三者是同一文件**（`nginx.conf` 主 server 内 `include /etc/nginx/*.conf;`）。由运维/上层在此写入应用 `location`。

**本项目落地路由**（写入 `${VOLUMES_PATH}/nginx/routes-app.conf`，`docker exec nginx nginx -t && nginx -s reload` 后生效）：
```nginx
location = /apps/factorymind { return 301 /apps/factorymind/; }
location ^~ /apps/factorymind/ {
    access_by_lua_file /etc/nginx/lua/page_access.lua;   # 入口仅校验登录态（同 "/" 与 "/home"）
    proxy_set_header Host $host;
    # 尾斜杠剥离 /apps/factorymind/ 前缀后再转发
    proxy_pass http://host.docker.internal:8080/;
}
```
- **前缀剥离的必要性**：SPA 的 `base:'./'` + `BASE_URL='./api'` 使 `./api` 在 `/apps/factorymind/` 下解析为 `/apps/factorymind/api/...`；`proxy_pass` 尾斜杠把前缀替换为 `/`，故转发到 web 容器的是 `/api/...`，再由 web nginx 反代到引擎。**若用绝对 `/api`，请求会打到网关根的 `/api/`（被 chat2db 占用）——这正是 `apps/web/src/api/client.ts` 必须改为相对路径的原因。**
- **勿用容器名做 upstream**：本机网关经 `host.docker.internal:8080` 指向正在运行的 FactoryMind web 容器（已实测可达）。若改走 supOS 网络容器名（`<appName>-<containerName>`，样板为 `agv-monitor-backend:8080`），须先把容器接入 `supos_default_network` 并确认 DNS 解析，否则 `nginx -t` 会因 upstream 解析失败而**中断网关**。

**验收证据**（`ENTRANCE_PORT=18088`，带 `supos_community_token` Cookie）：
- `GET /apps/factorymind/` → **200 **`text/html`（`.js` 相对引用 `./assets/...`）；
- `GET /apps/factorymind/api/health` → **200** `{"status":"ok","dataProvider":"mock","aiProvider":"mock","version":"0.1.0"}`（网关 → web → 引擎全链路通）；
- `GET /apps/factorymind/assets/index-*.js` → **200** `application/javascript`；
- 无 Cookie → **302** `/supos-login`（`page_access.lua` 生效）；
- **浏览器端到端（真机）**：LAN IP 登录 supOS → `/apps/factorymind/#/overview` 正常渲染（KPI 卡片/图表/6 项导航），`/apps/factorymind/api/*`（health/factories/lines/stations/metrics）**全部成功**，子页面（`#/line`）跳转正常。

**菜单/模块元数据不被 CE 消费（重要限制）**：
- 包内 `data/menu.yaml`、`data/module.yaml`、`data/syscode.yaml` 在 CE 下**不会生效**——CE 后端源码**无任何 `menu.yaml`/`module.yaml`/`syscode.yaml` 引用**，`supOS-CE.openapi.yaml` 也**无菜单接口**；`installStepFlags` 仅 `["front","i18n","route"]`（无 menu 步）。
- ⇒ 门户侧边栏**不会出现本应用入口**（实测确认）。菜单/模块集成属**上层应用商店**职责。CE 环境下演示/接入请直接访问 **`http://<host>:18088/apps/factorymind/`**（或在门户内以外部链接/iframe 方式挂载）。

### SupOS Provider 真机取数闭环（实测 2026-10-12）

**目标**：`DATA_PROVIDER=supos` 时，引擎真正从真机读到 UNS 层级 + 时序数据（非 mock）。

**① UNS 层级：`GET /uns/tree` 不可用 → 改 `POST /uns/condition/tree`**

- `GET /inter-api/supos/uns/tree` 的路由（`router.go` L2577）**根本不解析 `pageNo/pageSize`**，只接 `parentId/key/type/pathType`；服务端 `normalizeTreeQuery` 把 `PageSize` 强制为 10 ⇒ 无论怎么传都**只回 10 行**（`LAZY_TREE=true`）。
- `POST /inter-api/supos/uns/condition/tree`（body 走 `interUnsTreeConditionPayload`）**才认 `pageSize`**：`{"pageNo":1,"pageSize":500,"searchType":1}` → 一次返回全部 **64 节点**（扁平，含 `parentAlias`/`extend.factorymindType`）。
- 已据此把 `SupOsEndpoints.nodes` 改为该路径 + POST body；层级重建结果：`FAC_DEMO_01`(factory) → `LINE_A/LINE_B`(line) → 每线 5 个 station → `EQ_*`(equipment)/`PR_*`(process)。

**② 历史读写不一致（本构建缺陷）→ 同名视图对齐**

- **写**：MQTT `{"value":x}` → backend `applyIncomingCurrentValueUpdate` → `writeCurrentValueHistory` → 写入 **`postgresql` 容器** `public.supos_timeserial_double`（列 `tag, "timeStamp", status, value`，**按节点 id 作 tag**）。
- **读**：`POST /inter-api/supos/uns/file/history/timeseries/query` 用 `historyStoragePhysicalTable(detail, alias)` 解析物理表；本构建 `detail.Table` 为空 → **回退用 alias 当表名** → `relation "<ALIAS>" does not exist (42P01)`。
- ⇒ 即"**写进共享表、读到别名表**"，两侧永不重合。
- **修复（无需重建 supOS 镜像）**：为 45 个时序节点在 `postgresql.public` 建**同名视图**，映射到共享表：
  `create view public."<alias>" as select "timeStamp", value, coalesce(status,0) as quality from public.supos_timeserial_double where tag = <id>;`
  脚本：`services/ai-engine/scripts/seed_supos_history.py views`（幂等，`drop ... cascade` 后重建）。

**③ 历史查询的两个隐蔽约束**

- **时间戳必须带时区**：`parseHistoryTimeValue` 只匹配 RFC3339 系布局；发 `2026-10-12T10:30:00`（无偏移）→ HTTP 400 `uns.hist.startTime.invalid`。引擎侧 `_iso()` 已改为显式输出 `+00:00`。
- **默认分页仅 20 行**：不传 `pageSize` 时 `simpleHistoryPaging` 取默认 20 ⇒ 8h 窗口（≈480 点）被静默截断。引擎侧已显式传 `pageSize: 2000`。

**④ 数据回填 + 常驻喂数**

- `seed_supos_history.py backfill --hours 8`：直写共享表，回填 45 节点 × 481 样例（含 `ST_A40` 最近 2h 扭矩异常爬升故事线）；`on conflict do nothing` 幂等。
- `seed_supos_history.py feed --interval 5`：常驻 MQTT 喂数，保持 8h 窗口始终有新鲜数据（**录屏前须确保在跑**）。
- 实测：MQTT 发到 **alias 主题或 path 主题均可**（两者都能命中节点，`getLastMsg?alias=` 读回一致），且都会追加一行到共享表（`tag` 计数随之增长）。

**⑤ 端到端验收证据（2026-10-12）**

- 引擎直连：`GET localhost:8100/api/health` → `dataProvider: supos`；`/factories` → `示范总装工厂`；`/metrics?metric=torqueNokRate` → **4266 点**（9 站 × 474），`ST_A40` mean 2.54 / 末值 11.07%（其余站 ≈1.4%）——异常故事线真实可读。
- 经 supOS 网关：`GET 192.168.1.65:18088/apps/factorymind/api/{health,factories,metrics}` 带 `supos_community_token` Cookie 全部 **200** 且返回真机数据。

---

## 9. 下一步落地动作

1. ✅ **已完成** — 修正 `supos.py` 的 `SupOsEndpoints`（§7），并更新 `docs/SUPOS_INTEGRATION.md`。
   `nodes` → `POST /uns/condition/tree`（`GET /uns/tree` 受 `LAZY_TREE` 限制只回 10 行，见 §8"Provider 真机取数"块），移除 `events`，新增 `current_values` → `/uns/file/current/batchQuery`。
2. ✅ **已完成（2026-10-11）** — 本地 open supOS CE（`supos-ce-deploy` `V1.5.0.0-M`，Docker Desktop / WSL2）已拉起：
   9 核心服务 + grafana 全部 `Up`；网关 `http://localhost:18088` 可达（`/`→302 `/supos-login`）；
   默认账号 `supos`/`supos` 登录验证通过。起栈命令与实测结论见 §8。
3. 🔄 **进行中（10/11 起）** — §8 六项复核：第 **1、2、3、6** 项已闭环并回填（见 §8 表；第 2/3 项为应用包安装与路由实测，见 §8"应用包安装与网关路由"块）。
   已据真机实测修正鉴权载体：`/inter-api/supos/*` 由 Bearer 头改为 `supos_community_token` Cookie，
   涉及 `HttpSupOsClient` 与 `seed_uns.py`/`verify_supos.py`/`register_supos_route.py`.
   `/v3/api-docs` 在此构建为 404（Swagger 未启用），改用 `/inter-api/supos/supOS-CE.openapi.yaml`（需 Cookie）+ 源码复核。
   ✅ **提前完成（10/11）** — 装配线质量 UNS 模型已**导入真机并核验**：
   `seed_uns.py --apply --verify` 创建/更新 **64 节点**（19 文件夹 + 45 时序文件）；
   `GET /uns/tree?parentId=1` 确认层级正确——`FAC_DEMO_01` → `LINE_A`（25 子）/`LINE_B`（20 子）。
   实测踩坑并修复两处：**① `name` 格式**——supOS 限制 `^[\p{Han}A-Za-z0-9_-]+$`，
   模型里的空格/`#`（如 `电动拧紧枪 #1`、`一次合格率 FPY`）会被拒为 `uns.name.format.error`，
   已在 `seed_uns.py` 集中净化 `name`（保留 `displayName` 为人类可读）；**② 父级字段**——`parentAlias` 有效（非 `parentId`）。
   ⚠️ 注意：`LAZY_TREE=true` 时默认 `GET /uns/tree` 只返回约 10 条的惰性抽样、且不含层级，须传 `parentId` 逐层遍历或改用 `condition/tree`。
   ✅ **MQTT 事件通路已实测闭环（2026-10-11）**：向节点 path 主题发 `{"value":..}`，`getLastMsg` 即读回该值
   （topic 规则：`USE_ALIAS_PATH_AS_TOPIC=false` ⇒ 用 path；明细见 §8"MQTT 事件通路"块）。
4. ✅ **已完成** — 设计并落地 FactoryMind 的**装配线质量 UNS 模型**（§4 建模落点）。
   事实来源 `config/uns_model.json`、导入脚本 `scripts/seed_uns.py`、契约测试 `tests/test_uns_model.py`、
   文档 [`UNS_MODEL.md`](./UNS_MODEL.md)。
5. ✅ **已完成** — 按 §6 形态 A 打包为"标准应用形态"（对标 `spl-pl-distribution`）。
   交付包在 [`../supos-app/`](../supos-app/README.md)：`app.yaml`/`build.yaml`/`compose.yaml`/`vendor.yaml`/
   `open_api.yaml`/`config.ini`/`data/`/`statics/`/`bin/`，打包脚本 `supos-app/scripts/package-app.ps1`
   产出 `quality-copilot-app.zip`。前端已适配子路径部署（HashRouter + Vite `base: './'`）。
6. ✅ **已完成（2026-10-11）** — 应用包**已上传并安装进真机**（`installed`），网关路由 `/apps/factorymind/` **已打通并验收**
   （页面 200、`./api` 全链路通、无 Cookie 302 登录）。事实与复现命令见 §8"应用包安装与网关路由"块；
   前置代码修正：`apps/web/src/api/client.ts` 的 `BASE_URL` 由 `/api` 改为相对 `./api`。

---

## 10. 大赛提交要求与赛程（2026 上海开源软件应用创新大赛）

### 10.1 报名备案信息（提交材料须与此完全一致）

| 项 | 值 |
|---|---|
| 项目名称 | **FactoryMind AI —— 基于 open supOS 的总装质量异常根因分析与处置闭环工业 App** |
| 参赛赛道 | AI+ 工业软件赛道 |
| 企业赛题 | 【AI +工业软件赛道】open supOS 赛题：基于 open supOS 的工业 APP 开发与开源代码贡献 |
| 参赛方式 | 个人参赛（负责人身份信息不在公开仓库披露） |
| 状态 | 报名审核通过，已获参赛席位（以最新一次报名为准） |

### 10.2 硬性节点

| 项 | 值 |
|---|---|
| 提交截止 | **2026-10-16 24:00** |
| 提交方式 | 材料打包上传附件，发送至 `oscc@oschina.cn` |
| 官方网站 | https://www.oschina.net/os2026/ |
| 联系方式 | `oscc@oschina.cn` / 18760169917 |

### 10.3 提交三件套

1. **代码仓库链接** — 包含完整可运行的代码。
2. **作品介绍文档（推荐 PDF）** — 包含项目背景、技术架构、应用场景及创新点。
3. **作品演示视频链接** — 需展示作品的核心功能与使用流程。注意是**链接**而非文件，须先托管到可公开访问的位置。

### 10.4 作品要求（评审约束）

1. 需提交完整的作品方案介绍文档与项目源代码。
2. 必须为原创，不得侵犯第三方著作权、商标权及其他知识产权，不得违反法律法规。
3. 作品应能正常运行且达到命题预期效果，**与设计文档描述的功能一致**；若未实现设计文档中描述的全部功能，应注明**未实现功能、占比及完成程度**。
4. 代码注释应清晰、简洁、准确描述设计思路、功能与原理。

### 10.4-3 未实现功能与完成度（完整性自查 · 2026-10-11）

> 依据 §10.4 第 3 条：作品须与设计文档功能一致，若未实现须注明**未实现功能、占比及完成程度**。
> 本表按「代码 + 设计文档 + 真机实测」三重取证。

**口径定义**

- **分母 = 27 项**（设计文档承诺 24 项 + 设计外扩展 3 项）。来源：`README.md` §1 闭环 / §4 supOS 集成 / §6 运行模式，`CHANGELOG.md`、`docs/UNS_MODEL.md`、`docs/SUPOS_INTEGRATION.md`。
- **权重**：已实现 `1.0` / 部分实现 `0.5` / 仅 Mock·Stub `0.25` / 未实现 `0`；**完成度 = Σ权重 ÷ 27**。
- **状态含义**：`已实现`=功能完整可运行且有单测或真机验证；`部分实现`=主路径可用但有未覆盖分支或未真机验证；`仅 Mock·Stub`=接口与 schema 真实、内容为确定性占位；`未实现`=设计述及但代码缺失或被平台限制阻断。

**明细（27 项）**

| # | 功能项 | 域 | 状态 | 权重 | 证据 |
|---|---|---|---|---|---|
| 1 | 异常检测（中位数/MAD + z-score + 事件合并/严重度） | 分析 | 已实现 | 1.0 | `engine/anomaly.py` + 单测 |
| 2 | 证据关联 | 分析 | 已实现 | 1.0 | `engine/rootcause.py`（证据加权） |
| 3 | 根因候选排序 | 分析 | 已实现 | 1.0 | `engine/rootcause.py` |
| 4 | FMEA 匹配（EXACT→NORMALIZED→SEMANTIC→LLM_CANDIDATE） | 分析 | 已实现 | 1.0 | `engine/fmea.py`（LLM 档默认关闭、可选） |
| 5 | 风险评分 RPN/AP | 分析 | 已实现 | 1.0 | `engine/risk.py` |
| 6 | 行动项 CRUD | 分析 | 已实现 | 1.0 | `routers/actions.py` |
| 7 | 效果验证 | 分析 | 已实现 | 1.0 | `engine/verify.py` |
| 8 | 确定性引擎 / AI 解释层分离 | 分析 | 已实现 | 1.0 | 架构层（README §1） |
| 9 | AI 解释（默认 `MockAiProvider`） | AI | 仅Mock·Stub | 0.25 | `providers/ai.py`；schema 真实、文本占位 |
| 10 | 真实 LLM（OpenAI-compatible） | AI | 部分 | 0.5 | `providers/ai_openai.py` 已实现；默认未启用、未真机验证 |
| 11 | Mock 合成数据源（seed=2026 可复现） | 数据 | 已实现 | 1.0 | `providers/mock.py` |
| 12 | SupOS Provider（UNS 树 + 历史时序 + 事件） | 数据 | 已实现 | 1.0 | `providers/supos.py` + 单测；**真机端到端取数已验收**（§8"Provider 真机取数"块）：层级 64 节点、`/metrics` 4266 点真实可读；事件按设计走 MQTT（见 #13） |
| 13 | supOS MQTT 事件源 | 数据 | 已实现 | 1.0 | `providers/supos_mqtt.py`；真机闭环 ✅ |
| 14 | UNS 模型导入（64 节点） | 平台 | 已实现 | 1.0 | `scripts/seed_uns.py`；真机 ✅ |
| 15 | 标准应用包（形态 A） | 平台 | 已实现 | 1.0 | `supos-app/`；真机安装 ✅ |
| 16 | 应用路由 / 网关可达 | 平台 | 已实现 | 1.0 | `routes-app.conf`；浏览器验收 ✅ |
| 17 | Keycloak OAuth2 授权码流程（应用内） | 平台 | 未实现 | 0.0 | 现仅静态 token Cookie 呈递 |
| 18 | 门户菜单 / 模块集成 | 平台 | 未实现 | 0.0 | CE 不消费 `menu.yaml`（平台限制） |
| 19 | 权限 / 租户隔离 | 平台 | 未实现 | 0.0 | 设计外扩展项 |
| 20 | `/open-api/`（apikey）外部免登录集成 | 平台 | 部分 | 0.5 | 鉴权通；`restapi/graphql`→502（CE 未部署 Hasura，§8-1） |
| 21 | 前端六页面 | 前端 | 已实现 | 1.0 | `App.tsx` / `AppShell.tsx` |
| 22 | i18n 中英切换 | 前端 | 未实现 | 0.0 | 中文硬编码，无框架 |
| 23 | 前端自动化测试 | 前端 | 未实现 | 0.0 | 仅引擎有测试 |
| 24 | 引擎单测（8 文件） | 工程 | 已实现 | 1.0 | `services/ai-engine/tests/` |
| 25 | CI（`.github/workflows/ci.yml`） | 工程 | 已实现 | 1.0 | 后端 pytest/ruff + 前端 typecheck/lint/build |
| 26 | 容器化（Dockerfile + compose） | 工程 | 已实现 | 1.0 | 两份 compose |
| 27 | 行动项持久化（DB） | 工程 | 未实现 | 0.0 | `ActionStore` 显式内存态（设计取舍） |

**结算**

| 功能域 | 项数 | 权重和 | 完成度 |
|---|---|---|---|
| A 核心分析闭环 | 8 | 8.00 | 100% |
| B AI 解释层 | 2 | 0.75 | 37.5% |
| C 数据接入 | 3 | 3.00 | 100% |
| D open supOS 平台集成 | 7 | 3.50 | 50.0% |
| E 前端 | 3 | 1.00 | 33.3% |
| F 工程化 | 4 | 3.00 | 75.0% |
| **合计** | **27** | **19.25** | **71.3%** |

| 状态 | 项数 | 占比 | 权重 |
|---|---|---|---|
| 已实现 | 18 | 66.7% | ×1.0 |
| 部分实现 | 2 | 7.4% | ×0.5 |
| 仅 Mock·Stub | 1 | 3.7% | ×0.25 |
| **未实现** | **6** | **22.2%** | ×0 |

**结论：加权完成度 19.25 ÷ 27 = 71.3%（≈71%）；未实现 6/27 = 22.2%。**
未实现 6 项：Keycloak 授权码流程（应用内）、门户菜单/模块集成、权限/租户隔离、i18n 中英切换、前端自动化测试、行动项持久化。

**真机验证 vs Mock 验证（§10.6）**：核心分析闭环与 supOS 集成（UNS 导入 / **Provider 真机取数** / MQTT 事件 / 应用包 / 网关路由）为**真机验证**；AI 解释为 **Mock 验证**（`AI_PROVIDER=mock` 默认）。指标数值支持 `DATA_PROVIDER=supos` 真机读取（已验收，见 §8"Provider 真机取数"块），示例配置默认仍为 `mock` 以保证零依赖可跑。

**验证缺口（非功能缺失，不计入分母）**

| 缺口 | 说明 | 来源 |
|---|---|---|
| 真实工况验证 | 全部指标基于合成数据；漂移/多故障叠加未验 | `BENCHMARK.md` §5-1 |
| 效率人时验证 | 仅算力耗时，未做用户研究 | `BENCHMARK.md` §5-4 |
| API 漂移核验 | `V1.3`↔`V1.5` 差异待验（本项目已锁定 `V1.5.0.0-M`） | 本文档 §8-5 |
| 数据模型缺口 | 工位 `ST-B30` 缺工艺记录，根因匹配退化 | `BENCHMARK.md` §5-3 |

### 10.5 里程碑倒排

| 日期 | 目标 | 状态 |
|---|---|---|
| 10/11 | 起真机 open supOS CE（容器拉起）+ 网关可达性与默认账号登录验证 | ✅ 已完成 |
| 10/12 | 用实例复核 §8 六项，回填 `SupOsEndpoints` 与注册脚本示例 | 🔄 提前启动（§8 1/6 已闭环；鉴权已改 Cookie） |
| 10/13 | `scripts/seed_uns.py` 导入装配线质量 UNS 模型 + 验证 MQTT 事件通路 | ✅ **两项均已提前完成**：UNS 导入 64 节点/层级核验 ✅；MQTT 事件通路实测闭环 ✅ |
| 10/14 | 安装 FactoryMind AI 标准应用包 + 录制 3~5 分钟真实环境演示视频 |
| 10/15 | 作品介绍文档定稿 PDF + 代码仓库整理 + 完整性自查（含"未实现功能占比"表） |
| 10/16 | 提交（预留缓冲，勿压线） |

### 10.6 风险与对策

- **真机验证是视频的前置**：演示视频要求"真实 open supOS 环境运行"，因此 §9 第 2~4 项未跑通前，视频无法开录——真机验证优先级最高。
- **若截止前真机无法跑通**：作品介绍文档须**如实区分"Mock 验证"与"真机验证"**，并按 §10.4-3 标注未实现功能、占比与完成程度，不得含糊其辞。
- **提交材料口径统一**：所有文档/PPT 中的版本号统一为 `V1.5.0.0-M`（§1），项目名称统一用 §10.1 的备案全称。

---

## 附录：核验来源

- 本地克隆：`_supos_ref/supos-ce-backend`、`_supos_ref/supos-ce-deploy`
- 权威 API 清单：`supos-ce-backend/INTER_API.md`（按后端实际路由生成）
- 应用清单/注册：`supos-ce-backend/internal/application/plugin/service.go`
- UNS 载荷结构：`supos-ce-backend/internal/domain/uns/types.go`
- 标准应用样板：`supos-ce-deploy/mount/backend/apps/spl-pl-distribution/`
- 部署说明：`supos-ce-deploy/README-CN.md`、`docs/compose-service-map.md`
- Gitee 组织：https://gitee.com/supos-community-edition