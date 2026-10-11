# FactoryMind AI

> **From Factory Data to Factory Action.**
> 面向汽车总装线关键工位的**质量异常 → 根因 → FMEA → 行动 → 验证**闭环助手，构建在 open supOS 之上。

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)
![React](https://img.shields.io/badge/react-19-61DAFB.svg)
![Tests](https://img.shields.io/badge/tests-119%20passed-success.svg)

---

## 1. FactoryMind AI 是什么？

一个**确定性引擎 + AI 解释层**分离的工业质量分诊系统。它把分散的产线数据（FPY、NOK 率、扭矩不合格率、节拍、工具故障率）自动转化为一条可审计的处置链：

```
异常检测 → 证据关联 → 根因候选排序 → FMEA 匹配 → 风险评分(RPN/AP) → 行动项 → 效果验证
```

核心原则：**确定性计算负责数值，LLM 只负责解释**。FPY / NOK / 节拍 / Z-score / RPN / AP 全部由确定性代码算出，可手算复核；AI 只做根因解释、证据关联与建议生成，且每条输出都带 `evidence / confidence / reasoning / source / timestamp`。

---

## 2. 为什么重要？

工厂**不缺数据，缺的是决策**。质量异常发生时，工程师要在多个系统间来回切换，靠经验判断根因、翻历史 FMEA、口头派活、事后凭感觉确认是否改善——过程不可追溯、经验不可沉淀、响应以小时计。

FactoryMind AI 把这条链路压缩到**秒级确定性计算 + 结构化建议**，并留下完整证据链与验证结果，让"为什么这么判"永远可复核。它解决的不是"再做一个大屏"，而是**从数据到行动之间的断点**。

---

## 3. 工业场景是什么？

**汽车总装线关键工位质量异常**（唯一主场景）。

- 典型工位：关键扭矩拧紧、底盘合装、车门装配、终检等。
- 典型异常：一次合格率（FPY）下降、扭矩不合格率上升、节拍延长、工具故障率上升。
- 异常由**注入事件**驱动，带明确时间因果链：设备/工艺扰动 → 指标越限 → 多指标联动 → 质量后果。
- 分析方法遵循 **4M1E**（人/机/料/法/环）与 **FMEA**（严重度 S / 发生度 O / 探测度 D）。

> 全部数据为**合成数据**，不含任何真实工厂、车型、供应商或人员信息（见 §9 数据安全）。

---

## 4. 如何使用 open supOS？

FactoryMind AI 以**独立容器**形式部署在 supOS 旁边，通过网关的**应用路由注册 API** 挂载到平台；数据侧复用 supOS 的 **UNS（统一命名空间）** 与历史时序接口。

| 能力 | supOS 侧 | 本项目侧 |
|---|---|---|
| 工厂/产线/工位/设备/工艺层级 | UNS 模型树（ISA-95） | `SupOSProvider.get_factories/lines/stations/equipment/processes` |
| 质量时序数据 | `POST /uns/file/history/timeseries/query` | `SupOSProvider.get_metrics` |
| 实时/事件数据 | MQTT(EMQX) + `external/topic2Uns` | `SupOSProvider.get_events` |
| 应用挂载（形态 B） | `POST /inter-api/supos/app/routes/register` | `scripts/register_supos_route.py`（先发现实例 schema 再注册，见 [`docs/SUPOS_INTEGRATION.md`](docs/SUPOS_INTEGRATION.md) §8.2）；⚠️ 真机实测：本 CE 构建 nginx 只 `include /etc/nginx/*.conf`，形态 B 写入的子目录**不生效** |
| 标准应用包（形态 A，**真机验收路径**） | `/inter-api/supos/app/uploadApp` + `/app/install` | [`supos-app/`](supos-app/README.md)（`app.yaml`/`build.yaml`/`compose.yaml` + 打包脚本）；已上传安装并打通网关路由 `/apps/factorymind/`，见 [`docs/SUPOS_INTEGRATION_CHECKLIST.md`](docs/SUPOS_INTEGRATION_CHECKLIST.md) §8 |
| 认证 | Keycloak OAuth2 授权码流程 | `SupOSProvider` 获取/复用 token |

数据源通过 `IndustrialDataProvider` 抽象隔离，`DATA_PROVIDER` 环境变量在 **mock** 与 **supos** 之间切换，上层（引擎/页面）零改动。

> **集成基线**：锁定 open supOS `V1.5.0.0-M`。完整调研见 [`docs/SUPOS_RESEARCH.md`](docs/SUPOS_RESEARCH.md)，端点核验清单见 [`docs/SUPOS_INTEGRATION_CHECKLIST.md`](docs/SUPOS_INTEGRATION_CHECKLIST.md)。
> **当前状态**：`SupOSProvider` 已实现（UNS 树 / 历史时序 / 事件 → 领域模型），端点路径集中在 `SupOsEndpoints`，已对照 open supOS `V1.5.0.0-M` 后端路由表核验，并已在**真机实例**（CE `V1.5.0.0-M`，网关 `:18088`）完成 UNS 模型导入（64 节点）、MQTT 事件通路、标准应用包安装与网关路由端到端验收（见 [`docs/SUPOS_INTEGRATION_CHECKLIST.md`](docs/SUPOS_INTEGRATION_CHECKLIST.md) §8 与 §10.4-3）。未配置 `SUPOS_API_URL` 时 `DATA_PROVIDER=supos` 会显式报错，绝不静默降级为 mock。我们不把合成数据伪装成真实 supOS 数据。

---

## 5. 创新点是什么？

1. **确定性引擎与 AI 严格分离**：所有数值可复算、可审计，AI 不参与打分，杜绝"AI 拍脑袋"。
2. **FMEA 四级降级匹配，且敢于说"不知道"**：`EXACT → NORMALIZED → SEMANTIC → LLM_CANDIDATE → NO_MATCH`；无匹配时返回 `NO_MATCH` 并转人工，**绝不编造历史 FMEA**。
3. **证据加权根因排序**：`0.30·相关性 + 0.25·时序 + 0.20·幅度 + 0.15·FMEA相关 + 0.10·历史`，每一项都来自屏幕上的证据，可手算复核。
4. **闭环到"验证"**：行动项创建后，系统用**中位数恢复测量**验证异常是否达标（服务端保存异常快照，目标值与实测值均不接受客户端输入）。
5. **可复现基准**：合成数据集 + 固定种子（`--seed 2026`），基准报告由脚本生成、禁止手改，数字不会与引擎漂移（见 [`docs/BENCHMARK.md`](docs/BENCHMARK.md)）。

---

## 6. 如何运行？

### 前置条件

- Python **3.11+**
- Node **18+**，pnpm **9+**

### 后端（AI Engine，FastAPI）

```bash
cd services/ai-engine
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m app                      # → http://127.0.0.1:8100
```

### 前端（React + TS + Vite）

```bash
pnpm install
pnpm dev                           # → http://localhost:5173 （/api 代理到 :8100）
```

### 六个页面

| 路由 | 页面 | 作用 |
|---|---|---|
| `/` | Factory Overview | 工厂总览 KPI 与健康态势 |
| `/lines` | Line Monitor | 产线/工位指标监控 |
| `/anomalies` | Anomaly Center | 异常列表与严重度 |
| `/analysis` | AI Analysis | AI 结论 + 根因候选 + 证据链 + 推荐措施 |
| `/fmea` | FMEA & Risk | FMEA 四级匹配演示 + 风险矩阵 |
| `/actions` | Action & Verification | 行动项看板 + 效果验证 |

### Docker（推荐用于演示/部署）

```bash
cp .env.example .env
docker compose -f docker-compose.factorymind.yml up --build
#   web → http://localhost:8080      api → http://localhost:8100/api/health

# 接入既有 open supOS 部署（把两个容器接进 supos_default_network）
docker compose -f docker-compose.factorymind.yml -f docker-compose.supos.yml up --build
```

独立运行时默认 `DATA_PROVIDER=mock` / `AI_PROVIDER=mock`，无需 supOS 与 API key 即可跑通全闭环；
容器 HEALTHCHECK 打 `/healthz`（不触碰任何 Provider 的存活探针）。详见
[`docs/SUPOS_INTEGRATION.md`](docs/SUPOS_INTEGRATION.md) §8。

### 运行模式

| 模式 | 配置 | 数据来源 | 说明 |
|---|---|---|---|
| **Development（默认）** | `DATA_PROVIDER=mock` | `MockIndustrialDataProvider` | 确定性合成数据，无 supOS 实例也能跑通全闭环 |
| **Production / supOS** | `DATA_PROVIDER=supos` | `SupOSProvider` | 连接真实 open supOS（需 `SUPOS_API_URL`；端点已对照后端路由表核验）；事件经 MQTT 订阅（`SUPOS_EVENT_SOURCE=mqtt` + `SUPOS_MQTT_URL`） |

复制 [`.env.example`](.env.example) 为 `.env` 并按需填写（**切勿提交真实 `.env`**）。supOS 模式另需 `SUPOS_API_URL`（必填）与 `SUPOS_API_TOKEN`，以及事件通路的 `SUPOS_MQTT_URL`。详见 [`docs/SUPOS_INTEGRATION.md`](docs/SUPOS_INTEGRATION.md)。

### 接入真实 AI 模型（可选）

`AI_PROVIDER=mock` 是默认值，演示无需任何模型即可跑通。要接真实模型，只需换三个环境变量：

| 方式 | 配置 | 说明 |
|---|---|---|
| **本地 Ollama**（推荐，零成本、数据不出机） | `AI_PROVIDER=local`<br>`AI_BASE_URL=http://127.0.0.1:11434/v1`<br>`AI_MODEL=qwen2.5:7b-instruct` | 装 [Ollama](https://ollama.com/download) 后 `ollama pull qwen2.5:7b-instruct`；免 API key |
| **托管 API** | `AI_PROVIDER=openai-compatible`<br>`AI_BASE_URL=<服务商 OpenAI 兼容地址>`<br>`AI_API_KEY=<你的 key>`<br>`AI_MODEL=<模型名>` | 任何兼容 OpenAI chat-completions 的服务均可（DeepSeek / 阿里云百炼 Qwen / Moonshot / OpenAI 等） |

> 无论哪种，解释层的三条规则都在代码里强制（数值由引擎注入、证据引用必须存在、坏响应即报错），不依赖提示词。
> ⚠️ 托管 API 会把请求内容发往第三方：本项目演示数据为**合成数据**，请勿将真实产线数据接入外部端点。

### 测试与检查

```bash
# 后端
cd services/ai-engine
python -m pytest -q                # 119 passed
python -m ruff check app tests scripts

# 前端
pnpm typecheck
pnpm lint                          # oxlint
pnpm build
```

---

## 7. 如何复现基准测试？

```bash
cd services/ai-engine
python scripts/run_benchmark.py --seed 2026 --days 100
# 生成 docs/BENCHMARK.md（自动生成，请勿手改）
```

同参数重复运行结果**逐字节一致**（所有生成值都是时间戳与种子的纯函数）。报告覆盖异常检测阈值扫描、FMEA 四级匹配、根因 Top-1 与引擎耗时；全部为**合成数据上的 Demonstration / Simulation Result**，不代表真实工厂性能。

---

## 8. 如何贡献？

见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。简言之：先开 Issue 对齐方向 → 小步提交 → 本地跑通 `ruff` / `pytest` / `pnpm build` → 提 PR。版本变更见 [`CHANGELOG.md`](CHANGELOG.md)。

---

## 9. 数据安全

- 仓库内**全部为合成/匿名数据**，不含真实生产数据、车型信息、内部 FMEA 原件或任何账号密钥。
- 真实凭证只经环境变量注入，`.env` 已在 `.gitignore` 中排除。
- 详见 [`docs/LICENSE_AUDIT.md`](docs/LICENSE_AUDIT.md)（依赖合规）与 [`docs/SUPOS_RESEARCH.md`](docs/SUPOS_RESEARCH.md)（平台调研）。

---

## 10. 项目结构

```
factorymind-ai/
├── apps/web/                    # React 19 + TS + Vite + Tailwind + ECharts + Zustand
│   └── Dockerfile · nginx.conf  # 多阶段构建 SPA，nginx 托管并反代 /api
├── services/ai-engine/          # Python 3.11 + FastAPI + Pandas
│   ├── app/{engine,providers,routers,domain}/
│   ├── config/                  # anomaly_rules / assess_rules / master_fmea (可调参，非代码)
│   ├── scripts/                 # run_benchmark / verify_supos / backfill / register_supos_route
│   ├── Dockerfile               # 引擎镜像（非 root，内置 /healthz 探针）
│   └── tests/
├── packages/shared-types/       # 前后端共享类型
├── supos-app/                   # open supOS 标准应用包（形态 A：app.yaml/build.yaml/compose.yaml + 打包脚本）
├── docker-compose.factorymind.yml   # 独立运行（Mock 全闭环）
├── docker-compose.supos.yml         # override：接入 supOS 网关网络
├── docs/                        # SUPOS_INTEGRATION(_CHECKLIST) / SUPOS_RESEARCH / UNS_MODEL / BENCHMARK / LICENSE_AUDIT
├── .github/                     # Issue/PR 模板 + CI
├── .env.example
├── LICENSE  ·  CONTRIBUTING.md  ·  CHANGELOG.md
```

---

## 11. 技术栈

| 层 | 选型 |
|---|---|
| 前端 | React 19 · TypeScript · Vite · Tailwind · ECharts · Zustand |
| 后端 | Python 3.11 · FastAPI · Pandas · NumPy |
| 平台 | open supOS CE `V1.5.0.0-M`（UNS / 历史时序 / MQTT / Keycloak） |
| 质量 | ruff · pytest · oxlint · tsc · Vite build |

---

## 12. 许可证

本项目采用 **Apache-2.0**（见 [`LICENSE`](LICENSE)）。依赖全部为宽松许可，无 GPL/AGPL 强传染；详见 [`docs/LICENSE_AUDIT.md`](docs/LICENSE_AUDIT.md)。