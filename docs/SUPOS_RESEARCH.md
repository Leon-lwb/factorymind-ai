# SUPOS_RESEARCH.md — Open supOS Repository Discovery

> FactoryMind AI 项目 · 第一阶段调研文档
> 调研日期：2026-10-01
> 调研方式：Gitee 仓库（页面 + REST API）、官方文档站、官网产品页
> 状态：v1.0（Day 1 交付物）
>
> ⚠️ **本文为初版调研，部分结论已被源码核验取代。** 集成基线、端点路径、事件通路
> 的最新权威结论见 [`SUPOS_INTEGRATION_CHECKLIST.md`](SUPOS_INTEGRATION_CHECKLIST.md)：
> 基线升级为 `V1.5.0.0-M`；UNS 读取为 `GET /inter-api/supos/uns/tree`；
> supOS CE **无 REST 事件端点**，事件仅经 MQTT 接入。下文提及 `V1.3.0.0-M`
> 基线与 `uns/model/nodes`、`uns/event/query` 等处均已过时。

---

## 0. TL;DR — 我们的 App 如何基于 supOS 工作

**一句话回答（Day 1 验收标准）**：

FactoryMind AI 以**独立 Docker 容器**形式部署在 supOS 旁边，通过 supOS 网关（OpenResty，端口 8088）的**应用路由注册 API**（`POST /inter-api/supos/app/routes/register`）挂载到平台；数据侧通过 **UNS REST API** 建模工厂/产线/工位/设备层级（ISA-95 五级树），通过 **MQTT（EMQX :1883）+ `external/topic2Uns`** 注入实时质量数据，通过 **`/uns/file/history/timeseries/query`** 读取历史时序数据；认证走 **Keycloak OAuth2 授权码流程**。开发阶段无 supOS 实例时，由 `MockIndustrialDataProvider` 提供同构数据，接口签名与 `SupOSProvider` 完全一致。

```
┌─────────────────────────── supOS (Docker Compose) ───────────────────────────┐
│  EMQX(1883) ← MQTT 实时数据    Node-RED(1880) 采集流    Keycloak(8081) SSO    │
│  PostgreSQL/TimescaleDB(5432, 2345) 时序+关系存储                             │
│  backend(Go, :8080) — UNS CRUD / 历史查询 / Flow / 应用注册                    │
│  nginx/OpenResty(8088) 网关 — /inter-api/ /open-api/ /plugin/ + routes-app   │
└──────────────┬──────────────────────────────────────────────────┬────────────┘
               │ REST (UNS/历史/注册)                              │ MQTT
┌──────────────▼──────────────────────────────────────────────────▼────────────┐
│  FactoryMind AI（独立容器）                                                    │
│  前端 React18+TS+Vite（6 页面）  +  后端 Python FastAPI                        │
│  anomaly-engine / fmea-engine / ai-engine(Mock 可切换) / action-loop          │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. 仓库清单（Gitee 组织：supos-community-edition，共 12 个仓库）

| 仓库 | 语言 | 说明 | 与我们相关度 |
|---|---|---|---|
| `supos-ce-deploy` | Shell | 部署仓库（188★）：docker-compose、安装脚本、网关配置 | ★★★ |
| `supos-ce-backend` | Go | 后端（已重构为 Go；仓库描述仍写 Spring Boot，已过时） | ★★★ |
| `supos-ce-frontend` | TS/React | 前端：React 18 + Vite + antd 5 + Module Federation | ★★★ |
| `sup-os-edge` | Go | 边缘轻量版（2C4G/ARM） | ☆ |
| `supos-ce-fuxa` | NodeJS | FUXA 组态（已从主部署移除） | ☆ |
| `supos-ce-extension` | - | Chrome 插件 | ☆ |
| `go-zero` / `driver-go` / `json-iterator` / `di` / `share` / `readme` | Go | fork 的基础库 | ☆ |

来源：
- 组织主页：https://gitee.com/supos-community-edition
- 开放原子基金会公告（代码托管地址）：https://openatom.org/journalism/detail/PsYMumqYZ313

---

## 2. 关键发现表（按赛题要求格式）

| Topic | Finding | Source | Impact |
|---|---|---|---|
| **Frontend** | React 18.3.1 + TypeScript + Vite + pnpm；UI 库 antd 5.27.1；路由 react-router-dom 6.27；状态用 ahooks；**已移除 Tailwind**；采用 **@module-federation/vite**（host 名 `supos-ce/host`，暴露 components/utils/hooks/apis/stores）；Node 18+ | `supos-ce-frontend/package.json`、`vite.config.ts`（Gitee raw） | 我们的 React+TS+Vite 选型与 supOS 完全同构。Module Federation 是前端深度集成的官方机制（可作 P1/P2 增强）；首版我们保持独立 UI（深色工业控制塔风格），不强行套用 antd |
| **Backend** | **已从 Java/Spring Boot 重构为 Go**（仓库描述未更新，勿信）；DDD 分层：`internal/{application,domain,infrastructure,interfaces}`；infrastructure 含 blob/db/keycloak/mount/mqtt/nodered/realtime；DB 驱动 pgx/v5（PostgreSQL） | `supos-ce-backend` README、`go.mod`、`internal/` 目录 | 集成只依赖其 REST API，不依赖其内部实现；文档引用时注意"Java 版描述"是过时信息，避免在答辩材料中出错 |
| **UNS** | ISA-95 五级语义树（企业→站点→车间→产线→单元）；模型元素 Object/Attribute/Relationship/Event/Rule；REST CRUD：`POST /inter-api/supos/uns/model`（pathType 0=文件夹、2=文件；dataType 含时序/关系/计算/聚合/引用/JSONB）、`/uns/instance`、`/uns/batch` 批量建节点；历史查询 `POST /uns/file/history/timeseries/query`（body: alias, startTime, endTime） | `INTER_API.md`（supos-ce-backend）、官网架构页 | 我们的 Factory→Line→Station→Equipment→Process 模型可直接映射为 UNS 节点；质量记录建模为时序 dataType；SupOSProvider 只需封装这 3-4 个端点 |
| **Data Source** | OT：Modbus/OPC UA/S7/MQTT/Node-RED/自定义协议；IT：API/PostgreSQL/MySQL/SQL Server/MongoDB/MES/ERP/WMS；`POST /inter-api/supos/external/topic2Uns` 把外部 MQTT topic 注册为 UNS 节点；Flow 管理 API（flow/create 基于 UNS alias 生成 Node-RED 流程、flow/deploy） | `INTER_API.md`、官网 L2 层 | Demo 数据注入路径：脚本 → EMQX MQTT topic → topic2Uns → UNS 时序存储；与规格书 §10 的"可复现异常事件"设计完全兼容 |
| **Dashboard** | 前端内置 `dashboards` 页面；Grafana 11.5.6 为可选 profile（:3000）；FUXA 已从部署移除；BI 大屏 Agent 标注"测试中" | `docker-compose-8c16g.yml`、前端 `src/pages/`、官网 | 不依赖 supOS 仪表盘：我们自己实现 6 个页面（规格书 §16 要求）；Grafana 可选作补充展示 |
| **API** | 网关三类前缀：`/inter-api/supos/`（登录态）、`/service-api/supos/`、`/open-api/`（外部，部署 .env 有 `SUPOS_API_KEY`，但后端代码中未检索到实现，**待部署后验证**）；Swagger：`http://<host>:8088/swagger-ui`、`/v3/api-docs` | `INTER_API.md`、`supos-ce-deploy/.env`、网关路由配置 | 开发期以 `/inter-api/` 为准；部署后第一时间用 Swagger 核对实际端点，不凭文档猜测 |
| **Auth** | Keycloak 26.0，OAuth2 授权码流程；后端签发 JWT（golang-jwt/v5），cookie 名 `supos_community_token`（有效期 1 年）；端点：`GET /auth/code` → `GET /auth/token?code=` → `GET /auth/user` | `internal/interfaces/http/auth_routes.go`、compose 文件 | SupOSProvider 需实现 OAuth2 code flow 或复用浏览器 cookie；Mock 模式下跳过；答辩时"统一权限"是加分叙事 |
| **License** | 主协议 **MulanPSL-2.0**（木兰宽松，允许商用/修改）；NOTICE：Keycloak/Node-RED/EMQX/Grafana/TimescaleDB/Chat2DB = Apache-2.0，Portainer = Zlib，**MinIO = AGPL-3.0**；前端仓库 README 正文出现 "Apache-2.0" 字样与元数据 MulanPSL-2.0 不一致 | `supos-ce-deploy` LICENSE/NOTICE、`supos-ce-backend`、`supos-ce-frontend` | 我们项目可选 MulanPSL-2.0 或 Apache-2.0（兼容）；**禁止直接集成 MinIO SDK 分发**（AGPL 传染），只经 supOS API 间接使用；LICENSE_AUDIT.md 需记录此不一致 |
| **Deployment** | 纯 Docker Compose（无 K8s）；`bash bin/install.sh`（支持 --non-interactive --profiles）；规格 4C8G / 8C16G 两档；Docker Engine 27.4+ / Compose v2.31+；Windows 仅支持 WSL2，官方建议 8C16G100G；默认登录 supos/supos；网关端口 8088/8443 | `supos-ce-deploy` README、`bin/install.sh`、官方文档站 | 演示环境可在 WSL2 起一套；我们的 App 作为附加容器加入同一 compose 网络（`supos_default_network`），勿占用 8088/8443/1883/5432/2345/8081/3000/9000/1880/8000 |
| **App 集成机制** | ① 应用管理 API：`/app/install`、`/app/uploadApp`、`/app/uninstall`、`GET /app`；② **路由注册**：`POST /app/routes/register`（AppRouteRegistration）、`/app/routes/unregister`；③ backend 挂载 `third-apps` 目录与 docker.sock，可动态管理应用容器；④ 前端 Module Federation host | `INTER_API.md`、compose volume 挂载段 | 三条集成路径可选（见 §4），首版推荐"独立容器 + 路由注册" |
| **版本/成熟度** | 最新 Release `V1.3.0.0-M`（2026-01-22）；master 的 .env 已是 `V1.5.0.0-M`；提交活跃（调研前 1-3 天内有更新）；组织累计 538 PR | Release 页、`.env`、组织主页 | master 与 release 差距大 → **以 V1.3.0.0-M tag 为集成基线**，答辩材料注明版本号；避免追 master 导致 API 漂移 |
| **AI/Agent 能力** | 官网列出 MCP、Action Registry、Factory Agent、质量分析智能体；"对话式连接"测试中、"对话式建模/开发"规划中；npm 存在 `@iflow-mcp/mcp-server-supos`（get-topic-tree / get-topic-realtime-data / get-topic-history-data-by-pg）；deploy .env 有 `AGENT_DEPLOYMENT_URL=http://mcpclient:8123` | 官网、npmjs.com、deploy `.env` | 官方尚无现成"质量异常闭环"能力 → 我们的差异化成立；可在文档中引用 MCP 工具名佐证对生态的理解 |

---

## 3. 组件清单（当前 master 部署，已核对 compose 文件）

| 服务 | 镜像:tag | 性质 | 主机端口 |
|---|---|---|---|
| backend | `backend:26082808`（Go） | 核心 | 无（内部 8080） |
| nginx | `openresty:1.27.1.2-0-alpine` | 核心（Kong 兼容层，由 `generate_from_kong.py` 转换配置） | 8088 / 8443 |
| keycloak | `keycloak:26.0` | 核心 | 8081 |
| postgresql | `timescaledb:2.20.0-pg17` | 核心 | 5432 |
| tsdb | `timescaledb:2.20.0-pg17`（第二实例） | 核心 | 2345 |
| emqx | `emqx:5.8` | 核心 | 1883 / 8083 / 8084 / 18083(面板) |
| nodered | `node-red:4.0.8-22-supos` | 核心 | 1880 |
| chat2db | `chat2db:latest` | 核心 | 无 |
| portainer | `portainer-ce:2.23.0` | 核心 | 8000 / 9443 |
| grafana | `grafana:11.5.6` | 可选（profile: grafana） | 3000 |
| minio | `minio:RELEASE.2024-12-18` | 可选（profile: minio）⚠ AGPL | 9000 / 9001 |
| pi-coding-agent | `pi-agent:26090210` | 可选（profile: pi-agent） | 无 |

**与旧文档的差异（重要，答辩勿说错）**：Kong/Konga 无独立服务（由 OpenResty + Lua 模拟兼容层）；**TDengine 已被 TimescaleDB 取代**；Elasticsearch/Hasura/Redis/FUXA 已不在 compose 中。

---

## 4. FactoryMind 集成路径决策

| 路径 | 描述 | 优点 | 风险 | 决策 |
|---|---|---|---|---|
| A. 独立容器 + 路由注册 | App 独立 docker 化，调 `/app/routes/register` 挂到网关，前端经 `/plugin/` 或注册路由访问 | 解耦、可独立演示、Mock 可切换、符合规格书 §22/§23 | 需自行处理认证透传 | ✅ **首版采用** |
| B. Module Federation 远程模块 | 前端作为 MF remote 注入 supos-ce/host | 体验最"原生" | 耦合深、调试成本高、时间紧 | ⏸ 列为 P2 增强 |
| C. 纯 API 集成（无界面挂载） | 只消费 UNS/历史 API，独立访问入口 | 最简单 | "基于 supOS"的可见度弱 | 作为 A 的降级备份 |

**开发/生产双模式（规格书 §23 要求）**：
- `DATA_PROVIDER=mock` → MockIndustrialDataProvider（本地无 supOS 也能完整跑通闭环）
- `DATA_PROVIDER=supos` → SupOSProvider（封装 §2 表中的 UNS/历史/注册端点 + Keycloak OAuth2）

---

## 5. 风险与未决项

| # | 风险/未决项 | 等级 | 缓解 |
|---|---|---|---|
| 1 | `/open-api/` + `SUPOS_API_KEY` 在后端代码中未检索到实现 | 中 | 部署后用 Swagger 实测；首版走 `/inter-api/` |
| 2 | master(V1.5) 与 release(V1.3) 差距大，API 可能漂移 | 中 | 锁定 V1.3.0.0-M tag 为基线，README 注明 |
| 3 | MinIO AGPL-3.0 传染性 | 中 | 不直接依赖，仅经 API 间接使用；写入 LICENSE_AUDIT.md |
| 4 | 前端仓库 README 许可证表述自相矛盾（Apache-2.0 vs MulanPSL-2.0） | 低 | 以仓库 LICENSE 文件元数据（MulanPSL-2.0）为准，审计中记录 |
| 5 | 本地机器需支持 WSL2 + Docker（8C16G 建议） | 中 | Day 2 前验证本机环境；不满足则全程 Mock + 云端/备用机演示 |
| 6 | 官方在线使用手册（supos.com/devc/document?id=2114）暂无内容 | 低 | 以仓库内文档（INTER_API.md、README、docs/）为准 |
| 7 | 仓库描述过时（Java→Go、TDengine→TimescaleDB） | 低 | 所有引用以实际代码/文件为准，保留 Source 链接 |

---

## 6. 对规格书的修订建议

1. **§19/§22 数据源表述**：把"TDengine"相关假设改为 **TimescaleDB（PostgreSQL 17）**。
2. **§22 接口签名**：`getMetrics`/`getEvents` 映射到 `POST /uns/file/history/timeseries/query`（alias+时间范围）与 MQTT 实时订阅；`getFactories/getLines/getStations` 映射到 UNS 树查询。
3. **§23 集成原则**：补充"以 V1.3.0.0-M 为集成基线版本"。
4. **§34 许可证**：我们项目建议采用 **Apache-2.0**（与 MulanPSL-2.0 兼容、评审更熟悉），最终以 LICENSE_AUDIT 扫描为准。

---

## 7. 下一步（Day 2 输入）

1. 本机环境验证：WSL2 + Docker Desktop + 8C16G 可用性。
2. 项目脚手架：`apps/web`（React18+TS+Vite）+ `services/ai-engine`（FastAPI）+ `packages/shared-types`。
3. 定义 `IndustrialDataProvider` 接口（规格书 §22）并实现 Mock 版。
4. 部署验证（可并行）：WSL2 中拉取 `supos-ce-deploy` V1.3.0.0-M，`install.sh` 起最小集，实测 Swagger 与 §2 表中端点。

---

## 附录：主要来源

- 部署仓库：https://gitee.com/supos-community-edition/supos-ce-deploy
- 后端仓库：https://gitee.com/supos-community-edition/supos-ce-backend （INTER_API.md）
- 前端仓库：https://gitee.com/supos-community-edition/supos-ce-frontend
- 官网/文档：https://open.supos.com/zh-CN/ 、https://open.supos.com/zh-CN/docs/
- 产品页：https://www.supos.com/product/supos/ce
- 开放原子孵化公告：https://openatom.org/journalism/detail/PsYMumqYZ313
- MCP 服务（社区）：https://www.npmjs.com/package/@iflow-mcp/mcp-server-supos
