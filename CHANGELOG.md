# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Changed

- **集成基线升级到 open supOS `V1.5.0.0-M`**（原 `V1.3.0.0-M` 已落后两个版本）。
- **端点对照后端源码核验并修正**（依据 `supos-ce-backend` 路由表，非猜测）：
  - `SupOsEndpoints.nodes`：`/uns/model/nodes`（**该路径不存在**）→ `GET /inter-api/supos/uns/tree`，并新增 `_flatten_tree` 处理嵌套 `children`、补全子节点 `parentAlias`。
  - 移除 `SupOsEndpoints.events`：supOS CE **无 REST 事件查询端点**，`HttpSupOsClient.query_events` 改为显式抛 `SupOsUnavailableError`。
  - 新增 `SupOsEndpoints.current_values` = `POST /inter-api/supos/uns/file/current/batchQuery`（批量取当前值）。
- **`SUPOS_EVENT_SOURCE` 默认值 `rest` → `mqtt`**：`rest` 已不受支持（supOS CE 无事件端点），启动即 `ValueError` 并提示改用 MQTT。
- 同步更新 `docs/SUPOS_INTEGRATION.md`、`README.md`、`docs/LICENSE_AUDIT.md`；`docs/SUPOS_RESEARCH.md` 加注"结论已被源码核验取代"。
- `scripts/verify_supos.py` / `scripts/backfill_supos_endpoints.py`：目标从 `events` 改为 `current_values`，`rootAlias` 查询参数改为 `parentId`。
- 测试：`tests/test_supos_provider.py` 覆盖 UNS 树展平、当前值批量查询、REST 事件查询拒绝；`tests/test_supos_mqtt.py` 覆盖 `rest` 事件源被拒（119 用例通过）。

### Added

- **`docs/SUPOS_INTEGRATION_CHECKLIST.md`**：基于 Gitee 源码的集成对接清单（标准运行环境、认证、UNS 建模/读取端点、数据接入、两种应用集成形态、待实测项）。
- **`SupOSProvider`**（`services/ai-engine/app/providers/supos.py`）：open supOS 真实数据源适配器（spec §22 / §42）。
  - `SupOsClient` 协议 + `HttpSupOsClient`（httpx），端点路径集中在 `SupOsEndpoints`，可对照部署实例 Swagger 修正而不动业务逻辑。
  - UNS 树 → `Factory/Line/Station/Equipment/Process`（依据 ISA-95 `parent_alias` 重建层级）。
  - 历史时序 → `MetricPoint`（别名模板 `{station}_{metric}`，指标经 `METRIC_ALIAS_SLUGS` 转为后端合法别名；单位取自 `domain.METRIC_UNITS`）。
  - 事件 → `IndustrialEvent`（按工位作用域与类型过滤）。
  - 失败即报错：缺 `SUPOS_API_URL`、不可达、无法映射的枚举一律抛错，**绝不静默降级为 mock**（spec §23）。
  - 新增配置：`SUPOS_API_TOKEN` / `SUPOS_TIMEOUT_SECONDS` / `SUPOS_VERIFY_TLS` / `SUPOS_UNS_ROOT_ALIAS`。
- **supOS MQTT 事件通路**（`services/ai-engine/app/providers/supos_mqtt.py`）：`IndustrialDataProvider.query_events` 为拉取式时间窗查询，而 supOS 实时链路为 MQTT 推送，故新增 `SUPOS_EVENT_SOURCE=rest|mqtt`。
  - `EventBuffer`：有界时间窗缓存；`MqttEventIngestor`：订阅 EMQX topic、解析 JSON 载荷写入缓冲区，坏消息记录并丢弃（不伪造），连接中断按 `reconnect_seconds` 重连。
  - `MqttBackedSupOsClient`：UNS 树 / 历史时序仍走 HTTP，事件读自 MQTT 缓冲区，合成单一 `SupOsClient`。
  - 连接器 `MqttConnector` 可注入，默认惰性导入 `aiomqtt`，无 broker 亦可运行单元测试。
  - 新增配置：`SUPOS_EVENT_SOURCE` / `SUPOS_MQTT_URL` / `SUPOS_MQTT_TOPIC` / `SUPOS_USERNAME` / `SUPOS_PASSWORD`。
  - 新增运行时依赖 `aiomqtt`（BSD-3-Clause）；许可证审计已同步（`docs/LICENSE_AUDIT.md`）。
- **`docs/SUPOS_INTEGRATION.md`**：supOS 集成指南（端点核验清单、映射约定、REST/MQTT 双事件源、认证、故障语义）。
- 应用新增 lifespan：启动时拉起 MQTT 订阅（`astart`），关闭时停订阅并释放 HTTP 客户端（`aclose`）。
- 测试 `tests/test_supos_provider.py`：假客户端 + `httpx.MockTransport`，无需真实 supOS（+17 用例）。
- 测试 `tests/test_supos_mqtt.py`：假 `MqttConnector`，覆盖 URL 解析 / 载荷解析 / 缓冲区 / 摄取器 / 混合客户端与工厂选择（无需真实 broker）。
- **端点核验脚本** `services/ai-engine/scripts/verify_supos.py`：实例到位后一键核验 §3 两条待确认端点与 MQTT 载荷字段。
  - 抓取部署实例 `/v3/api-docs` 发现真实 UNS 相关路径（权威来源，优先于候选猜测）。
  - 探测"配置路径 + 发现路径 + 回退候选"，区分 404 / 401-403 / 2xx，标注当前配置与最佳替代。
  - 用真实 `SupOsProvider` 在最佳候选路径上端到端解析层级、指标与事件，并打印可粘贴的 `SupOsEndpoints(...)` 修正行。
  - 可选 `--mqtt-url` 采集真实 topic 与载荷字段，校验 `parse_event_payload` 的解析成功率与枚举取值。
  - 全程如实报告，退出码反映配置端点是否核验通过；`--json` 导出结构化结果。
- **端点回填工具** `services/ai-engine/scripts/backfill_supos_endpoints.py`：把核验报告里实测通过的路径写回 `SupOsEndpoints` 默认值。
  - 只回填实测 `OK` 的路径（2xx 且适配器信封解析器取到行）；仅 2xx 但解析不了的 `WARN` 候选默认跳过，需显式 `--allow-warn`。
  - 默认 dry-run 只打印 unified diff；`--write` 才落盘，且落盘前用 `ast.parse` 校验，绝不写入坏代码；路径已一致时幂等返回。
  - `--check` 模式供 CI 使用：只读比对默认值与核验报告，不一致即退出 `3`（代码漂移），与 `--write` 互斥。
- **AI 解释层真实化** `OpenAiCompatibleAiProvider`（`services/ai-engine/app/providers/ai_openai.py`，spec §6.1 / §7 / §20）：
  - 任何兼容 OpenAI chat-completions 的服务均可驱动（DeepSeek / Qwen / Moonshot / 本地 Ollama / OpenAI 本身），传输层为纯 `httpx`，不引入厂商 SDK。
  - **三条规则在代码里强制，而非交给提示词**：① 引擎拥有的数值（风险 S/O/D/RPN/AP、根因排序、时间戳）一律由请求注入，模型返回值被忽略；② 每条措施引用的 `evidence_ids` 必须真实存在，引用不存在的证据即**整体拒绝**；③ 非 JSON、缺字段、类型错、措施为空一律抛 `AiProviderError`，**不静默降级为 mock**（spec §23）。
  - 超时 / 重试：`408/429/5xx` 与传输异常按指数退避重试，`4xx` 直接失败；`AI_TIMEOUT_SECONDS` / `AI_MAX_RETRIES` 可配。
  - `AI_PROVIDER=openai-compatible` 需 `AI_BASE_URL` + `AI_MODEL` + `AI_API_KEY`；`AI_PROVIDER=local`（Ollama / vLLM / LM Studio）需前两者、免 key；缺参启动即报错，**不默认任何端点**。
  - `RecommendedAction` 新增 `evidence_ids` 字段（`app/domain/models.py` + `packages/shared-types/src/index.ts` 同步）。
  - 测试 `tests/test_ai_openai.py`：`httpx.MockTransport` 离线驱动，覆盖正常解析、markdown 围栏 / 前后缀包裹、证据引用硬校验、各类坏响应、重试与不可达（+21 用例）。
- **容器化**（spec §19 / §22）：
  - `services/ai-engine/Dockerfile`：python:3.11-slim，仅装运行时依赖，非 root 用户运行，内置 `/healthz` HEALTHCHECK。
  - `apps/web/Dockerfile` + `apps/web/nginx.conf`：多阶段构建 SPA，nginx 托管并反代 `/api` 到引擎容器，浏览器同源。
  - `docker-compose.factorymind.yml`：独立运行（Mock 全闭环，无需 supOS）；`docker-compose.supos.yml` 为附加 override，把两个容器接入既有 `supos_default_network`。
  - 主机端口避开 supOS CE 已占用端口（8088/8443/1883/5432/2345/8081/3000/9000/1880/8000）。
  - 两个 Dockerfile 均提供**可覆盖的镜像源参数**：`PIP_INDEX_URL`（默认阿里云 PyPI）、`NPM_REGISTRY`（默认 npmmirror，同时供 corepack 下载 pnpm）。实测公网源在本机仅 ~40 kB/s / ~280 kB/s，走镜像后分别提升到 ~4.3 MB/s，构建从 20 分钟级降到分钟级。
  - **实测验证**（Docker Desktop 4.86 / Engine 29.7.2）：两镜像均构建成功；`docker compose up -d` 后引擎 `Up (healthy)`（`depends_on: service_healthy` 生效），web 经 nginx 反代 `/api` 正常；`/healthz`、`/api/health`、`/api/factories`、`/api/anomalies` 全部返回预期数据。
  - 许可证审计新增 §5「容器基础镜像」，并**完成镜像内实测扫描**（Alpine 读 `apk` 数据库、Debian 读 `copyright`）：engine 87 条目 / web 68 包，GPL/LGPL 集中在基础 OS 层，**镜像内无 AGPL 授权组件**（两处 AGPL 命中经核查均为许可文本而非代码）。
- **`/healthz`**（`app/main.py`）：不触碰任何 Provider 的存活探针，供容器编排使用；`/api/health` 继续报告当前 Provider 配置。新增测试断言其不入 OpenAPI 契约。
- **装配线质量 UNS 模型**（spec §22）：
  - `services/ai-engine/config/uns_model.json`：模型唯一事实来源——`Factory→Line→Station→Equipment/Process` 层级（2 线 / 9 工位 / 64 节点）、`extendProperties.factorymindType` 节点类型标记、每工位 5 个时序文件（`pathType=2` + `dataType=1`）与事件 MQTT 约定。
  - `services/ai-engine/scripts/seed_uns.py`：把模型转为 `POST /inter-api/supos/uns/batch` 请求体；默认 dry-run 只打印，`--apply` 才导入并可选 `--verify` 回读树；发送前校验别名合法与父先于子，被拒节点原样报告后端错误并以非零码退出。
  - `services/ai-engine/tests/test_uns_model.py`：契约测试（别名合法 / 唯一 / 父先于子 / 每工位 5 文件 / 指标别名与 provider 模板一致 / 事件词表），钉住模型与适配器的接缝（+9 用例）。
  - **`docs/UNS_MODEL.md`**：模型说明（层级、别名硬约束、属性、时序文件、事件约定、导入与校验）。
- **open supOS 标准应用包**（形态 A，对标 `spl-pl-distribution`）：`supos-app/`
  - `app.yaml` / `build.yaml` / `compose.yaml` / `vendor.yaml` / `dependency.yaml` / `dependencies.yaml` / `requirement.yaml` / `open_api.yaml` / `config.ini` / `data/{module,menu,i18n,syscode}.yaml` / `statics/`。
  - `supos-app/bin/{backend,front}/Dockerfile`（镜像构建描述；源码由打包脚本暂存，单一事实源仍在 `services/ai-engine` 与 `apps/web`）。
  - `supos-app/scripts/package-app.ps1`：组装并产出可安装的 `quality-copilot-app.zip`（zip 条目强制正斜杠，兼容 Linux 端解压），可选 `-BuildImages` 构建两个镜像。
  - 前端适配子路径部署：`main.tsx` 改用 `HashRouter`、`vite.config.ts` 设 `base: './'`，使应用在任意 `/apps/<prefix>/` 下无需网关子路径重写即可运行。
- **路由注册脚本** `services/ai-engine/scripts/register_supos_route.py`：把 FactoryMind AI 挂到 supOS 网关。
  - **不猜平台 API**：先从部署实例 `/v3/api-docs` **发现** route/register 操作，打印实例声明的请求体 schema（含必填标记），再由操作者用 `--field key=value` / `--json-body` 显式提供字段。
  - 默认 dry-run 只打印将要发送的请求；`--apply` 才真正发送；`--list` 列出实例暴露的全部 route/register 操作；`--unregister` 走注销路径。
  - 退出码：`0` 成功（含 dry-run）、`1` 请求失败、`2` 参数不可用、`3` 未能发现端点。

### Changed

- `METRIC_UNITS` 上移至 `app/domain/models.py`，作为前后端共享的 `MetricPoint.unit` 单一事实源。
- `httpx` 提升为运行时依赖（原仅在 `requirements-dev.txt`）。
- 新增 AI 层配置：`AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` / `AI_TIMEOUT_SECONDS` / `AI_MAX_RETRIES`（`.env.example` 已同步）。
- `create_ai_provider` 支持 `openai-compatible` 与 `local`；未知取值显式报错而非回落 mock。

### Planned

- `SupOSProvider` 端点对照部署实例 Swagger 核验（`docs/SUPOS_INTEGRATION.md` §3）。
- 对照部署实例核验 MQTT topic 命名与载荷字段（`external/topic2Uns` 桥接规则），必要时调整 `SUPOS_MQTT_TOPIC` 与载荷解析字段名。
- 对照部署实例核验 `POST /app/routes/register` 的请求体字段，并据实回填注册脚本的示例（见 `docs/SUPOS_INTEGRATION.md` §9）。

## [0.1.0] — 2026-10-04

首个可运行版本：确定性质量分诊闭环（异常 → 根因 → FMEA → 风险 → 行动 → 验证）+ 基准测试 + 开源治理。

### Added

- **确定性引擎**（`services/ai-engine/app/engine/`）
  - `anomaly.py`：中位数/MAD 鲁棒基线、z-score 越限检测、事件合并、严重度与证据关联。
  - `fmea.py`：Master FMEA 四级降级匹配 `EXACT → NORMALIZED → SEMANTIC → LLM_CANDIDATE → NO_MATCH`；无匹配返回 `NO_MATCH` 并转人工。
  - `rootcause.py`：证据加权根因排序（相关性/时序/幅度/FMEA 相关/历史）。
  - `risk.py`：由 S/O/D 确定性派生 RPN 与 AP。
  - `verify.py`：基于中位数恢复测量的效果验证（样本不足返回 `None`）。
- **Provider 抽象**（`app/providers/`）
  - `IndustrialDataProvider` 协议 + `MockIndustrialDataProvider`（确定性、可复现合成数据）。
  - `AIProvider` + `MockAiProvider`（返回与真实模型一致的 schema）。
  - 通过 `DATA_PROVIDER` / `AI_PROVIDER` 环境变量切换；未实现的值**显式报错**而非静默降级。
- **HTTP API**（`app/routers/`）：工厂/产线/工位/设备/工艺/指标/事件、异常、`/analysis`、`/fmea/records`、`/fmea/match`、行动项 CRUD 与 `/actions/{id}/verify`、`/health`。
- **前端六页面**（`apps/web/`）：Factory Overview、Line Monitor、Anomaly Center、AI Analysis、FMEA & Risk、Action & Verification；AI 分析页可携带异常跳转创建行动。
- **基准测试**（`services/ai-engine/scripts/run_benchmark.py`）：异常检测阈值扫描、FMEA 四级匹配、根因 Top-1、引擎耗时；生成 `docs/BENCHMARK.md`（自动生成、禁止手改、同种子逐字节一致）。
- **文档**：`docs/SUPOS_RESEARCH.md`（open supOS 仓库调研）、`docs/BENCHMARK.md`、`docs/LICENSE_AUDIT.md`（依赖许可证审计）。
- **开源治理**：`README.md`、`LICENSE`（Apache-2.0）、`CONTRIBUTING.md`、`CHANGELOG.md`、`.github/`（Issue/PR 模板 + CI）。

### Validation

- 后端 `pytest`：56 passed。
- 后端 `ruff check`：通过。
- 前端 `tsc` / `vite build` / `oxlint`：通过。
- 基准（`--seed 2026 --days 100`）：异常检测 F1 97.5%（3.0σ 为 F1 拐点）、FMEA Level Accuracy 49/49、根因 Top-1 297/297、单次完整分诊约 2.0 ms。

### Security

- 仓库内全部为合成/匿名数据；真实凭证仅经环境变量注入，`.env` 已排除。
- 依赖均为宽松许可，无 GPL/AGPL；未集成 MinIO SDK（AGPL-3.0）。

[Unreleased]: https://example.com/factorymind-ai/compare/v0.1.0...HEAD
[0.1.0]: https://example.com/factorymind-ai/releases/tag/v0.1.0