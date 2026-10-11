# SUPOS_INTEGRATION.md — open supOS 集成指南

> FactoryMind AI 项目 · 数据源适配说明
> 集成基线：open supOS `V1.5.0.0-M`（见 [`SUPOS_INTEGRATION_CHECKLIST.md`](SUPOS_INTEGRATION_CHECKLIST.md)）
> 状态：适配器已实现；端点路径已对照后端路由表核验，并已在真机实例（CE `V1.5.0.0-M`）完成 UNS 导入 / MQTT 事件通路验收（见 [`SUPOS_INTEGRATION_CHECKLIST.md`](SUPOS_INTEGRATION_CHECKLIST.md) §8）

---

## 1. 它是什么

`SupOSProvider`（`services/ai-engine/app/providers/supos.py`）实现与
`MockIndustrialDataProvider` 相同的 `IndustrialDataProvider` 协议，把 open supOS 的
**UNS 树 / 历史时序 / 事件**读成引擎已在消费的领域模型。切换只发生在环境变量层：

```env
DATA_PROVIDER=mock    # 开发默认：确定性合成数据
DATA_PROVIDER=supos   # 生产：连接真实 open supOS
```

上层（异常引擎 / FMEA / 页面）零改动。

**两条不可协商的规则：**

1. **绝不编造数据。** supOS 不可达、节点缺失、或线上值无法映射为领域枚举时，适配器**抛错**，
   不会退化成合成数据。缺 `SUPOS_API_URL` 时 `DATA_PROVIDER=supos` 直接报错。
2. **绝不猜测 API。** Provider 只依赖 `SupOsClient` 协议；HTTP 传输与端点路径集中在
   `HttpSupOsClient` / `SupOsEndpoints`，可对照部署实例的 Swagger 修正，而不触碰任何业务逻辑。

---

## 2. 配置项

| 环境变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `DATA_PROVIDER` | 是 | `mock` | 置 `supos` 启用本适配器 |
| `SUPOS_API_URL` | 是 | — | 网关地址，如 `http://<host>:8088`；缺失即报错 |
| `SUPOS_API_TOKEN` | 建议 | — | 呈递给网关的 token，以 `supos_community_token` Cookie 发送（见 §5 认证） |
| `SUPOS_TIMEOUT_SECONDS` | 否 | `10` | HTTP 超时（秒） |
| `SUPOS_VERIFY_TLS` | 否 | `true` | 校验 TLS 证书；自签证书环境可置 `false` |
| `SUPOS_UNS_ROOT_ALIAS` | 否 | — | 限定 UNS 遍历起点（如厂区根节点 alias） |
| `SUPOS_EVENT_SOURCE` | 否 | `mqtt` | 事件来源，仅支持 `mqtt`（supOS CE 无 REST 事件端点，见 §4.4） |
| `SUPOS_MQTT_URL` | 条件 | — | `SUPOS_EVENT_SOURCE=mqtt` 时必填，如 `mqtt://<host>:1883` |
| `SUPOS_MQTT_TOPIC` | 否 | `factorymind/uns/#` | 订阅的通配 topic |
| `SUPOS_USERNAME` / `SUPOS_PASSWORD` | 否 | — | MQTT broker 凭据 |

> 真实 token 只经环境变量注入，**绝不写入仓库**；`.env` 已在 `.gitignore` 中。

---

## 3. 端点映射与核验清单

`SupOsEndpoints` 中的路径均已对照 open supOS `V1.5.0.0-M` 后端路由表
（`internal/interfaces/http/router.go`）核验，非猜测。完整依据见
[`SUPOS_INTEGRATION_CHECKLIST.md`](SUPOS_INTEGRATION_CHECKLIST.md)。

| 用途 | 路径 | 来源 | 状态 |
|---|---|---|---|
| UNS 树读取 | `POST /inter-api/supos/uns/condition/tree` | 后端路由表（body: `pageNo, pageSize, searchType`，返回扁平列表，含 `parentAlias`）。**不用 `GET /uns/tree`**：该 GET 路由不解析分页，受 `LAZY_TREE` 限制只回 10 行 | 真机已验收 |
| 历史时序查询 | `POST /inter-api/supos/uns/file/history/timeseries/query` | 后端路由表（body: `alias, startTime, endTime`，**时间戳须带时区、`pageSize` 须显式指定**） | 真机已验收 |
| 当前值批量查询 | `POST /inter-api/supos/uns/file/current/batchQuery` | 后端路由表（body: `[alias, ...]`） | 源码已核验 |
| 事件读取 | —（无 REST 端点） | supOS CE 未提供事件查询 REST 接口 | 事件仅经 MQTT 接入 |

**首次生产运行前**，即使真机已验收，仍建议在目标部署实例上（若启用 Swagger）打开
`http://<host>:18088/swagger-ui`（或 `/v3/api-docs`）复核实际路径与请求体；若与上表有差异，
只需在构造 `HttpSupOsClient` 时传入修正后的 `SupOsEndpoints`，或在 `create_data_provider`
中调整默认值 —— 业务逻辑无需改动。注：本项目实测的 CE `V1.5.0.0-M` 构建**未启用** Swagger
（`/v3/api-docs` 返回 404），端点核验以源码路由表与实机调用为准。

### 3.1 一键核验脚本

上述核验已脚本化，直接对运行中的实例执行即可：

```bash
cd services/ai-engine
python scripts/verify_supos.py --base-url http://<host>:8088 --token <bearer>
# 有 MQTT 时一并核验事件载荷：
python scripts/verify_supos.py --base-url http://<host>:8088 --token <bearer> \
    --mqtt-url mqtt://<host>:1883
```

脚本依次执行四步，并**如实报告**（不猜、不编造通过）：

1. **网关可达性 + OpenAPI 发现**：抓取 `/v3/api-docs`，列出所有 UNS 相关路径与摘要 ——
   这是端点核验的权威来源，优先于任何候选猜测。
2. **候选路径探测**：对"配置路径 + OpenAPI 发现路径 + 少量回退候选"逐一发起请求，
   区分 `HTTP 404`（路径不对）、`401/403`（路径在、token 被拒）、`2xx`（可用），
   并标注哪条是当前配置、哪条是最佳替代。
3. **适配器端到端解析**：用**真实 `SupOsProvider`** 驱动最佳候选路径，逐级重建
   工厂→产线→工位→设备/工艺，并查询指标与事件；能解析到哪一层、卡在何处一目了然。
4. **MQTT 事件流**：连接 broker、订阅 `SUPOS_MQTT_TOPIC`、收集若干秒消息，
   输出实际 topic 命名、载荷字段集合、`parse_event_payload` 的解析成功率与
   枚举取值，用于比对 `SUPOS_MQTT_TOPIC` 与载荷字段名。

报告末尾直接给出可粘贴的 `SupOsEndpoints(...)` 修正行；`--json <path>` 可导出结构化结果
（可能含真实产线数据，**请勿提交仓库**）。退出码 `0` 表示配置的三条端点均核验通过，`1` 表示
至少一条配置端点失败（即需要按建议修正）。

### 3.2 回填工具

核验通过后，把实测路径写回 `SupOsEndpoints` 默认值，无需手工编辑源码：

```bash
cd services/ai-engine
python scripts/backfill_supos_endpoints.py --report report.json          # 先看 diff（默认 dry-run）
python scripts/backfill_supos_endpoints.py --report report.json --write  # 确认后落盘
python scripts/backfill_supos_endpoints.py --report report.json --check  # CI：漂移即失败
```

行为约定：

- **只回填实测 OK 的路径**（HTTP 2xx 且适配器的信封解析器取到了行）。仅 2xx 但无法解析的
  WARN 候选默认跳过 —— 解析不了就不算核验通过，把它写进代码等于把猜测固化成默认值；
  确有需要可显式 `--allow-warn`。
- **默认 dry-run**：不带 `--write` 只打印 unified diff，不碰文件。
- **绝不写入坏代码**：改写后的模块先经 `ast.parse` 校验，语法不过即中止，不落盘。
- **幂等**：路径已一致时报告 `already up to date`，不产生多余改动。
- **`--check` 供 CI 使用**：只读比对，默认值与核验报告不一致即退出 `3`，防止已核验路径被无声
  改回候选值；与 `--write` 互斥。

退出码：`0` 成功（含 dry-run、`--check` 一致与幂等），`1` 报告/源码读取或改写失败，
`2` 无任何可回填的已核验端点，`3` `--check` 检测到代码漂移。

> 落盘后请运行 `python -m pytest -q`，并同步更新 §3 表格的"状态"列与 `docs/SUPOS_RESEARCH.md`
> 的相关记录 —— 脚本会打印同样的提醒，但不会替你改文档。

---

## 4. 数据映射约定

### 4.1 UNS 节点 → 层级

每个 UNS 节点被归一化为 `SupOsNode`，其 `node_type` 取
`factory | line | station | equipment | process | other`，由节点的
`factorymindType / type / nodeType / level` 经同义词表映射得到（如
`enterprise|site|plant → factory`、`workcenter|unit|cell → station`）。
`parent_alias` 承载 ISA-95 父子链，Provider 据此重建：

```
get_factories()      ← node_type = factory
get_lines(factory)   ← node_type = line   AND parent_alias = factory
get_stations(line)   ← node_type = station AND parent_alias = line
get_equipment(station) ← node_type = equipment AND parent_alias = station
get_processes(station) ← node_type = process   AND parent_alias = station
```

- 设备的 `Equipment.type` 取自节点属性 `equipmentType`（或 `type`），须为
  `TORQUE_TOOL | PLC | SENSOR | ROBOT | CONVEYOR` 之一；缺失或非法即报错。
- 工艺的 `Process.operation` 取自节点属性 `operation`，缺省回退为节点名。

### 4.2 指标别名约定

时序别名由模板 `{station}.{metric}` 生成（`SupOsProvider(metric_alias_template=...)` 可改），
例如 `ST-A40.fpy`、`ST-A50.cycleTime`。`metric` 取值即 `MetricKey` 的 wire 值
（`fpy / nokRate / torqueNokRate / cycleTime / toolErrorRate`）。
单位由 `MetricPoint.unit` 契约（`app/domain/models.py::METRIC_UNITS`）给出。

### 4.3 事件

事件记录按 `stationAlias` 归属工位；`type / severity` 须分别映射为
`IndustrialEventType` / `RiskLevel` 枚举，无法映射即报错（避免错误标注污染风险展示）。

### 4.4 事件来源：MQTT

supOS 的实时链路是 MQTT（EMQX），`external/topic2Uns` 把外部 topic 桥接进 UNS。
`IndustrialDataProvider` 的 `query_events` 是**拉取式时间窗查询**，而 MQTT 是推送流，
因此 `SUPOS_EVENT_SOURCE` 只提供一种落地：

| 取值 | 实现 | 数据通路 |
|---|---|---|
| `mqtt`（唯一支持） | `MqttBackedSupOsClient` | EMQX topic ─▶ `MqttEventIngestor` ─▶ `EventBuffer` ─▶ `query_events` |
| `rest`（已废弃） | — | supOS CE 无 REST 事件端点，启动即 `ValueError` |

> `HttpSupOsClient.query_events` 直接抛 `SupOsUnavailableError`，绝不静默返回空 ——
> 无事件端点时必须显式失败，而不是让上层以为"这段时间没有异常"。

`MqttBackedSupOsClient` 把两条通路合到一个 `SupOsClient` 上：UNS 树与历史时序仍走 HTTP，
事件读自 MQTT 缓冲区。订阅在应用启动时由 FastAPI lifespan 拉起（`astart`），关闭时停掉（`aclose`）。

**载荷约定**：MQTT 消息体为 JSON 对象，字段与 REST 事件行一致
（`timestamp / stationAlias / eventType / severity / message / equipmentAlias`）。
若载荷未带 `stationAlias`，回退取 topic 的最后一个非通配段作为工位 alias。
无法解析的消息被记录并丢弃，**不会**被伪造成事件；订阅循环在连接中断后按
`reconnect_seconds` 重连。

> MQTT 连接器是可注入的（`MqttConnector`），默认实现惰性导入 `aiomqtt`。
> 因此无 broker 也能运行单元测试（见 §7）。

---

## 5. 认证

当前实现采用**静态 token，经 Cookie 呈递**：token 由 `SUPOS_API_TOKEN` 注入，
`HttpSupOsClient` 在每个请求加 `Cookie: supos_community_token=<token>`。

> **【真机实测 2026-10-11】** supOS CE 网关（OpenResty）对 `/inter-api/supos/*` 的守卫
> 由 lua `router_lib.lua::get_supos_token()` 实现，**只读 `supos_community_token` Cookie**：
> `Authorization: Bearer <token>` 会被直接拦截为 **401**，而同一 token 放进该 Cookie 则 **200**。
> 因此适配器必须用 Cookie 呈递（原 Bearer 实现已在真机证伪并修正）。

Cookie 的取值就是 Keycloak 的原始 `access_token`。授权码流程
（`GET /auth/code → /auth/token?code= → /auth/user`）是部署期关注点，本适配器不重复实现。
生产环境有两种落地方式：

1. 由部署侧取得 token 注入 `SUPOS_API_TOKEN`（真机已验证：Keycloak `supos` client 允许
   `password` 授权，可用 `curl -d grant_type=password ... username=supos password=supos` 直接换取）；
2. 在网关前置一层反向代理完成登录态透传（研究文档 §4 路径 A 的认证透传）。

> 后续若需要容器内自助取 token，可在 `SupOsClient` 协议之外增加一个 token provider，
> 不影响现有代码路径。

---

## 6. 故障语义

| 场景 | 行为 |
|---|---|
| `SUPOS_API_URL` 缺失 | 启动即 `ValueError` |
| 连接失败 / 超时 / HTTP ≥ 400 / 响应非 JSON | `SupOsUnavailableError` |
| 响应信封无可识别行列表 | `SupOsError` |
| 节点缺 alias、时序值非数值、时间戳不可解析 | `SupOsError` |
| 设备类型 / 事件类型 / 严重度无法映射 | `SupOsError` |
| `SUPOS_EVENT_SOURCE=mqtt` 但缺 `SUPOS_MQTT_URL` | 启动即 `ValueError` |
| `SUPOS_EVENT_SOURCE=rest`（supOS CE 不支持） | 启动即 `ValueError` |
| `SUPOS_EVENT_SOURCE` 非法取值 | 启动即 `ValueError` |
| MQTT 消息体非 JSON / 非对象 / 无工位 alias | 记录并丢弃该消息（`SupOsError`，订阅不中断） |
| MQTT 连接中断 / broker 重启 | 记录告警，按 `reconnect_seconds` 重连 |
| 无匹配工位、窗口倒置 | 返回空元组（非错误） |

所有错误都向上冒泡，**不会**回退到 mock —— 见 §1 规则 1。

---

## 7. 测试

两个测试文件覆盖，均无需真实 supOS 或 broker：

`services/ai-engine/tests/test_supos_provider.py`

- `SupOsProvider` × 假 `SupOsClient`：层级映射、别名约定、事件作用域与类型过滤、
  失败即报错的各类输入、生命周期释放。
- `HttpSupOsClient` × `httpx.MockTransport`：`supos_community_token` Cookie、请求体、`Z`/`+08:00` 时间归一化、
  数值强转、错误状态与坏 JSON 的抛错、信封解析。

`services/ai-engine/tests/test_supos_mqtt.py`

- `MqttEventSettings.from_url`：TLS/端口/凭据解析与非法 URL。
- `parse_event_payload`：载荷 alias、topic 回退、坏载荷抛错。
- `EventBuffer`：窗口过滤与容量上限。
- `MqttEventIngestor` × 假 `MqttConnector`：填充缓冲区、丢弃坏消息、启停生命周期。
- `MqttBackedSupOsClient`：HTTP/MQTT 分流与生命周期；工厂 `rest`/`mqtt` 选择与缺参报错。

```bash
cd services/ai-engine
python -m pytest tests/test_supos_provider.py tests/test_supos_mqtt.py -q
```

---

## 8. 部署与路由注册

### 8.1 镜像与编排

FactoryMind AI 以两个容器交付（spec §19 / §22）：

| 容器 | 镜像 | 说明 |
|---|---|---|
| `factorymind-engine` | `services/ai-engine/Dockerfile` | FastAPI 引擎；仅装运行时依赖，非 root 运行，内置 `/healthz` 探针 |
| `factorymind-web` | `apps/web/Dockerfile` | 多阶段构建的 SPA，由 nginx 托管并反代 `/api` 到引擎，浏览器同源 |

```bash
# 独立运行：Mock Provider，全闭环，无需 supOS
cp .env.example .env
docker compose -f docker-compose.factorymind.yml up --build
#   web → http://localhost:8080      api → http://localhost:8100/api/health

# 接入既有 supOS 部署（网关需经共享网络访问 web 容器）
docker compose -f docker-compose.factorymind.yml -f docker-compose.supos.yml up --build
```

`docker-compose.supos.yml` 把两个容器接入 supOS CE 已创建的 `supos_default_network`
（`external: true`，需已存在；用 `docker network ls` 确认）。主机端口刻意避开 supOS CE
占用的 8088/8443/1883/5432/2345/8081/3000/9000/1880/8000。

> **构建加速**：两个 Dockerfile 默认走国内镜像源（PyPI 用阿里云、npm 用 npmmirror），
> 因为公网源在目标网络实测只有 ~40 kB/s / ~280 kB/s，会让构建从分钟级变成小时级。
> 换环境时覆盖即可：
> `docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple ...`、
> `docker build --build-arg NPM_REGISTRY=https://registry.npmjs.org ...`。

> **健康检查**：容器 HEALTHCHECK 打 `/healthz` —— 该端点不触碰任何 Provider。supOS 网关
> 变慢时，编排器不应因此重启容器，而应让它作为**数据源故障**暴露出来；`/api/health` 才是
> 报告当前 Provider 配置的接口。

### 8.2 路由注册

网关需要一条路由指向 web 容器，`/factorymind` 才会解析。调研定位到端点
`POST /inter-api/supos/app/routes/register`，但**未确认请求体字段**——本项目不猜平台 API，
因此脚本采用"先发现、再注册"：

```bash
cd services/ai-engine

# 1) 先看实例声明的请求体 schema（不发请求）
python scripts/register_supos_route.py --base-url http://<host>:8088 --token <bearer>

# 2) 按 schema 显示的字段注册（示例字段名，须以上一步输出为准）
python scripts/register_supos_route.py --base-url http://<host>:8088 --token <bearer> \
    --field name=factorymind --field path=/factorymind \
    --field target=http://factorymind-web:80 --apply

# 列出实例暴露的全部 route/register 操作
python scripts/register_supos_route.py --base-url ... --token ... --list
```

行为约定：

- **不发明字段**：脚本从实例 `/v3/api-docs` 发现 route/register 操作，打印其请求体
  schema（含必填标记与字段说明），字段值由操作者用 `--field key=value` 或
  `--json-body <file>` 显式提供；`--field` 的值按 JSON 解析，故 `enabled=true` 是布尔而非字符串。
- **默认 dry-run**：不带 `--apply` 只打印将要发送的请求；`--apply` 才真正发送。
- **发现不到 OpenAPI 时**降级用候选路径 `/inter-api/supos/app/routes/register`，并明确标注
  "未经实例核验"。
- 退出码：`0` 成功（含 dry-run）、`1` 请求失败、`2` 参数不可用（如请求体为空）、
  `3` 未能发现端点。

### 8.3 部署顺序

1. supOS CE 已 `install.sh` 起好，`docker network ls` 能看到 `supos_default_network`。
2. `docker compose ... -f docker-compose.supos.yml up --build` 起两个容器。
3. `curl http://localhost:8100/healthz` 返回 `{"status":"ok"}`。
4. 按 §3.1 核验 supOS 端点（`verify_supos.py`），必要时回填 `SupOsEndpoints`。
5. 按 §8.2 注册网关路由，再从网关地址访问 `/factorymind`。

---

## 9. 待办

- [ ] **部署实例上核验 §3 两条待核验端点**，必要时修正 `SupOsEndpoints`
      （本机暂无 supOS 实例、官方文档站不可达，此步尚无法执行）。
      脚本已就绪：`python scripts/verify_supos.py --base-url http://<host>:8088 --token <bearer>`（见 §3.1）；
      核验通过后用 `python scripts/backfill_supos_endpoints.py --report report.json --write` 写回默认值（见 §3.2）。
- [ ] 核验 MQTT topic 命名与载荷字段（`external/topic2Uns` 的实际桥接规则），
      必要时调整 `SUPOS_MQTT_TOPIC` 与 `parse_event_payload` 的字段名。
      用同一脚本的 `--mqtt-url` 步骤采集真实载荷字段（见 §3.1）。
- [ ] 核验 `POST /app/routes/register` 的请求体字段（见 §8.2），据实回填注册脚本的示例。
- [ ] 按需引入容器内 Keycloak token 刷新（见 §5）。