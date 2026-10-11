# LICENSE_AUDIT.md — 依赖许可证审计

> FactoryMind AI · 开源治理交付物（规格书 §34 / §35）
> 审计日期：2026-10-04（2026-10-08 增量复核：`aiomqtt` / `paho-mqtt`，随 supOS MQTT 事件通路引入；容器基础镜像，随容器化引入）
> 审计方式：**本机实际扫描 + 上游官方来源核对**，非文档推断
> - Python：`importlib.metadata` 读取已安装分发的 `License-Expression` / `License` / `Classifier`
> - 前端：`pnpm licenses list --json`
> - 容器基础镜像：镜像内包许可数据库实测（Alpine `apk` / Debian `copyright`），见 §5
> 复核命令见文末 §8。

---

## 1. 结论（TL;DR）

1. **本项目采用 Apache-2.0**（见根目录 `LICENSE`，与根 `package.json` 的 `"license": "Apache-2.0"` 一致）。
2. 全部直接与传递依赖均为**宽松许可**（MIT / BSD / Apache-2.0 / ISC / 0BSD / PSF），**无 GPL / AGPL / SSPL 等强传染许可**。
3. 唯二的弱传染许可为 **MPL-2.0**（Python `certifi`；前端 `lightningcss`）——均为**未修改的原样使用**，且 `lightningcss` 仅参与构建期，不随产物分发，**不触发文件级开源义务**。
4. **未引入 MinIO**：supOS 官方部署中的 MinIO 为 AGPL-3.0，本项目只经 supOS API 间接使用对象存储能力，**不链接、不分发其 SDK**（规格书硬约束）。
5. 本项目**不分发 supOS 任何源代码**，仅以独立容器在运行期调用其 HTTP/UNS 接口；因此本项目许可证与 supOS 上游许可（MulanPSL-2.0）相互独立且兼容。
6. **容器基础镜像含 OS 级组件**：实测两个镜像（`factorymind-ai-engine` 87 条目 / `factorymind-ai-web` 68 包），GPL/LGPL 集中在基础 OS 层（`busybox`、`apk-tools`、Debian 基础包等），**镜像内无 AGPL 授权组件**（§5）。这些组件未修改、未链接，不构成衍生作品，不使本项目受 GPL 约束。本项目自身依赖树（§3、§4）**无 GPL / AGPL**。

> ⚠️ 本页数字来自**本机已安装依赖**的实测。依赖版本随安装解析而变；升级依赖后请重跑 §8 命令并更新本文件。

---

## 2. 上游 supOS 平台许可（不随本项目分发，仅作兼容性核对）

来源：`supos-ce-deploy` LICENSE / NOTICE（Gitee，调研见 `docs/SUPOS_RESEARCH.md` §2）。

| 组件 | 许可 | 与本项目的关系 |
|---|---|---|
| supOS CE 主体（backend/frontend/deploy） | **MulanPSL-2.0**（木兰宽松） | 仅运行期经 HTTP/UNS 调用；不复制代码。MulanPSL-2.0 为宽松许可，与 Apache-2.0 兼容 |
| Keycloak / Node-RED / EMQX / Grafana / TimescaleDB / Chat2DB | Apache-2.0 | 平台内部组件，本项目不直接依赖 |
| Portainer | Zlib | 同上 |
| **MinIO** | **AGPL-3.0** ⚠ | **本项目禁止直接集成其 SDK**；仅经 supOS API 间接使用 |
| 前端仓库 README 正文 | 正文写 "Apache-2.0"，元数据为 MulanPSL-2.0 | 以上游仓库 `LICENSE` 文件元数据（MulanPSL-2.0）为准；此为上游文档不一致，已在风险表记录 |

**基线版本**：锁定 `V1.5.0.0-M`（当前 Release）为集成基线，避免追 master 造成 API 漂移。

---

## 3. Python 依赖（`services/ai-engine`）

### 3.1 直接运行时依赖（`requirements.txt`）

| 依赖 | 版本 | 许可 | 用途 | 风险 |
|---|---|---|---|---|
| fastapi | 0.142.2 | MIT | HTTP 服务框架 | 无 |
| uvicorn | 0.54.0 | BSD-3-Clause | ASGI 服务器 | 无 |
| pydantic | 2.13.5 | MIT | 数据模型与校验 | 无 |
| httpx | 0.28.1 | BSD-3-Clause | open supOS 适配器 HTTP 客户端 | 无 |
| aiomqtt | 2.5.1 | BSD-3-Clause | supOS 实时事件 MQTT 订阅 | 无 |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | 数值计算（中位数/MAD、z-score） | 无（多许可择一，均为宽松） |
| pandas | 3.0.6 | BSD-3-Clause | 时序聚合 | 无 |

### 3.2 直接开发依赖（`requirements-dev.txt`）

| 依赖 | 版本 | 许可 | 用途 | 风险 |
|---|---|---|---|---|
| pytest | 9.1.1 | MIT | 单元/集成测试 | 无 |
| httpx | 0.28.1 | BSD-3-Clause | 测试客户端 / HTTP 调用 | 无 |
| ruff | 0.16.9 | MIT | Lint / 格式化 | 无 |

### 3.3 传递依赖（实测已安装）

| 依赖 | 版本 | 许可 | 风险 |
|---|---|---|---|
| starlette | 1.7.0 | BSD-3-Clause | 无 |
| annotated-types | 0.8.0 | MIT | 无 |
| annotated-doc | 0.0.5 | MIT | 无 |
| pydantic_core | 2.46.5 | MIT | 无 |
| typing-inspection | 0.4.4 | MIT | 无 |
| typing_extensions | 4.16.0 | PSF-2.0 | 无 |
| anyio | 4.15.1 | MIT | 无 |
| h11 | 0.16.0 | MIT | 无 |
| httpcore | 1.0.9 | BSD-3-Clause | 无 |
| idna | 3.20 | BSD-3-Clause | 无 |
| certifi | 2026.7.22 | **MPL-2.0** | 低：弱传染，未修改原样使用 |
| python-dateutil | 2.9.0.post0 | Dual (BSD / Apache-2.0) | 无 |
| six | 1.17.0 | MIT | 无 |
| tzdata | 2026.4 | Apache-2.0 | 无 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | 无 |
| pluggy | 1.6.0 | MIT | 无 |
| iniconfig | 2.3.0 | MIT | 无 |
| Pygments | 2.21.0 | BSD-2-Clause | 无 |
| colorama | 0.4.6 | BSD-3-Clause | 无 |
| paho-mqtt | 2.1.0 | EPL-2.0 OR BSD-3-Clause | 无（双许可，本项目择 BSD-3-Clause） |
| opentelemetry-api | 1.45.0 | Apache-2.0 | 无 |

**Python 侧结论**：无强传染许可；`certifi`（MPL-2.0）仅作为 CA 证书束未修改使用，风险可忽略。
`paho-mqtt` 为 `EPL-2.0 OR BSD-3-Clause` 双许可，本项目按 BSD-3-Clause 使用，规避 EPL-2.0 的弱传染条款。

---

## 4. 前端依赖（`apps/web`，含 workspace 传递依赖）

### 4.1 直接运行时依赖

| 依赖 | 版本 | 许可 | 用途 | 风险 |
|---|---|---|---|---|
| react | 19.3.0 | MIT | UI 框架 | 无 |
| react-dom | 19.3.0 | MIT | DOM 渲染 | 无 |
| react-router-dom | 7.18.4 | MIT | 路由 | 无 |
| zustand | 5.0.15 | MIT | 状态管理 | 无 |
| echarts | 6.1.0 | Apache-2.0 | 图表可视化 | 无 |

### 4.2 直接开发 / 构建依赖

| 依赖 | 版本 | 许可 | 用途 | 风险 |
|---|---|---|---|---|
| typescript | 6.0.3 | Apache-2.0 | 类型系统 | 无 |
| vite | 8.3.2 | MIT | 构建 / 开发服务器 | 无 |
| @vitejs/plugin-react | 6.1.1 | MIT | React 插件 | 无 |
| oxlint | 1.86.0 | MIT | Lint | 无 |
| tailwindcss | 4.3.3 | MIT | 样式 | 无 |
| @tailwindcss/vite | 4.3.3 | MIT | Tailwind 构建插件 | 无 |
| @types/node | 24.19.0 | MIT | 类型声明 | 无 |
| @types/react | 19.3.0 | MIT | 类型声明 | 无 |
| @types/react-dom | 19.3.0 | MIT | 类型声明 | 无 |

### 4.3 传递依赖（实测已安装，`pnpm licenses list`）

| 依赖 | 版本 | 许可 | 风险 |
|---|---|---|---|
| @jridgewell/gen-mapping | 0.3.13 | MIT | 无 |
| @jridgewell/remapping | 2.3.5 | MIT | 无 |
| @jridgewell/resolve-uri | 3.1.2 | MIT | 无 |
| @jridgewell/sourcemap-codec | 1.6.0 | MIT | 无 |
| @jridgewell/trace-mapping | 0.3.31 | MIT | 无 |
| @oxc-project/types | 0.152.0 | MIT | 无 |
| @oxlint/binding-win32-x64-msvc | 1.86.0 | MIT | 无（平台二进制） |
| @rolldown/binding-win32-x64-msvc | 1.2.12 | MIT | 无（平台二进制） |
| @rolldown/pluginutils | 1.0.1 | MIT | 无 |
| @tailwindcss/node | 4.3.3 | MIT | 无 |
| @tailwindcss/oxide | 4.3.3 | MIT | 无 |
| @tailwindcss/oxide-win32-x64-msvc | 4.3.3 | MIT | 无（平台二进制） |
| cookie | 1.1.1 | MIT | 无 |
| csstype | 3.2.3 | MIT | 无 |
| detect-libc | 2.1.2 | Apache-2.0 | 无 |
| enhanced-resolve | 5.26.0 | MIT | 无 |
| fdir | 6.5.0 | MIT | 无 |
| graceful-fs | 4.2.11 | ISC | 无 |
| jiti | 2.7.0 | MIT | 无 |
| lightningcss | 1.32.0 / 1.33.0 | **MPL-2.0** | 低：弱传染，构建期未修改使用，不随产物分发 |
| lightningcss-win32-x64-msvc | 1.32.0 / 1.33.0 | **MPL-2.0** | 低：同上 |
| magic-string | 0.30.21 | MIT | 无 |
| nanoid | 3.3.19 | MIT | 无 |
| picocolors | 1.1.1 | ISC | 无 |
| picomatch | 4.0.7 | MIT | 无 |
| postcss | 8.5.28 | MIT | 无 |
| react-router | 7.18.4 | MIT | 无 |
| rolldown | 1.2.12 | MIT | 无 |
| scheduler | 0.28.0 | MIT | 无 |
| set-cookie-parser | 2.7.2 | MIT | 无 |
| source-map-js | 1.2.2 | BSD-3-Clause | 无 |
| tapable | 2.3.3 | MIT | 无 |
| tinyglobby | 0.2.17 | MIT | 无 |
| tslib | 2.3.0 | 0BSD | 无 |
| undici-types | 7.24.6 | MIT | 无 |
| zrender | 6.1.0 | BSD-3-Clause | 无 |

**前端结论**：无强传染许可；`lightningcss`（MPL-2.0）为构建期依赖，不进入交付产物。

---

## 5. 容器基础镜像（随容器化引入）

扫描方式：**镜像内实测**。构建两个镜像后直接读取镜像内的包许可数据库——
Alpine 读 `/lib/apk/db/installed` 的 `L:` 字段（逐包自带许可），
Debian 读 `/usr/share/doc/*/copyright` 的 `License:` 字段。

| 镜像 | 基底 | 规模 | 主要许可分布 | AGPL |
|---|---|---|---|---|
| `factorymind-ai-engine:0.1.0` | Debian（`python:3.11-slim`） | 87 个 doc 条目 | GPL-2+ ×19、GPL-3+ ×10、LGPL-2.1+ ×8、GPL-2 ×5、BSD-3 ×5、MIT/X11 ×4、Apache-2.0 ×3 | **无** |
| `factorymind-ai-web:0.1.0` | Alpine（`nginx:1.27-alpine`） | 68 个包 | MIT ×16、GPL-2.0-only ×7、BSD-3-Clause ×6、X11 ×6、BSD-2 ×2、Apache-2.0 ×2 | **无** |

基础镜像本体许可：Python **PSF-2.0**、Node.js **MIT**（仅构建期，不进最终镜像）、nginx **BSD-2-Clause**。
来源：<https://docs.python.org/3/license.html>、<https://nodejs.org/en/about/legal>、<https://nginx.org/>。

**AGPL 专项核查**（本项目明确规避 AGPL，如 MinIO）：全库检索仅两处命中，**均非 AGPL 授权的代码**。

| 命中文件 | 实际内容 | 结论 |
|---|---|---|
| `/usr/share/doc/base-files/copyright` | `Files: licenses/AGPL-*` —— 该包**附带 AGPL 许可文本**（`/usr/share/common-licenses/AGPL-3`） | 许可文本，非代码 |
| `/usr/share/doc/ca-certificates/copyright` | MPL-2.0 条款正文将 AGPL-3.0 列为 "Secondary License" 选项 | 许可定义文本，非代码 |

**判定**：

- 上述 GPL / LGPL 组件（`busybox`、`apk-tools`、`alpine-baselayout`、`scanelf`、Debian 基础包等）
  属于**基础 OS 层**，本项目**未修改、未链接**，以独立进程 / OS 工具形式存在，**不构成衍生作品**，
  不触发本项目代码的 GPL 义务。
- `node:22-alpine` 只在构建阶段使用，最终镜像为静态文件 + nginx，Node 运行时不进入交付产物。
- 本项目自身依赖树（§3、§4）**无 GPL / AGPL**；镜像内亦**无 AGPL 授权组件**。

---

## 6. 风险清单与缓解

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| 1 | MinIO（AGPL-3.0）经 supOS 间接存在 | 中 | 不集成其 SDK、不链接、不分发；仅经 supOS HTTP API 使用 |
| 2 | `certifi` / `lightningcss` 为 MPL-2.0（弱传染） | 低 | 未修改原样使用，不产生文件级开源义务；记录在案 |
| 3 | 上游 supOS 前端仓库 README 许可表述自相矛盾 | 低 | 以其 `LICENSE` 文件元数据（MulanPSL-2.0）为准 |
| 4 | supOS master 与 release 之间的 API 漂移 | 中 | 锁定 `V1.5.0.0-M` 为基线；端点已对照后端路由表核验，并已在真机 CE `V1.5.0.0-M` 实例验收（见 [`SUPOS_INTEGRATION_CHECKLIST.md`](SUPOS_INTEGRATION_CHECKLIST.md) §8） |
| 5 | 依赖升级引入新许可 | 低 | CI 中保留许可扫描步骤（见 §8），升级后重跑 |
| 6 | 容器基础镜像的 OS 包未逐包扫描（§5） | 低 | Docker 可用后跑 `trivy` / `syft` 产出镜像 SBOM；GPL-2.0 仅存在于未修改的 OS 层 |

---

## 7. 本项目许可证决策

- **选择：Apache-2.0**。
- 依据：本项目自身代码为原创；不与 supOS 代码形成衍生作品（仅运行期调用其 API），故许可选择独立。Apache-2.0 为评审最熟悉、生态最兼容的宽松许可，且与上游 MulanPSL-2.0 兼容。
- `NOTICE`：本项目不复制上游代码，故无需为 supOS 生成 NOTICE 条目；如未来引入第三方代码，须在此补充其归属声明。

---

## 8. 复核命令（可复现）

```bash
# Python 依赖许可（在 services/ai-engine 的虚拟环境中）
python - <<'PY'
import importlib.metadata as m
for d in sorted(m.distributions(), key=lambda x: x.metadata.get('Name','').lower()):
    md = d.metadata
    lic = md.get('License-Expression') or md.get('License') or 'UNKNOWN'
    print(f"{md.get('Name','')}|{d.version}|{lic}")
PY

# 前端依赖许可（仓库根目录，需已安装依赖）
pnpm licenses list
pnpm licenses list --json > /tmp/fm-npm-licenses.json

# 依赖漏洞审计（规格书 §35）
pnpm audit
pip-audit            # 若未安装：python -m pip install pip-audit

# 容器基础镜像许可（§5；需 Docker 守护进程运行）
docker build -t factorymind-ai-engine:0.1.0 ./services/ai-engine
docker build -f apps/web/Dockerfile -t factorymind-ai-web:0.1.0 .

# Alpine（web）：逐包许可直接取自 apk 数据库的 L: 字段
docker run --rm --entrypoint sh factorymind-ai-web:0.1.0 \
    -c "cat /lib/apk/db/installed" | grep -E '^(P|L):'

# Debian（engine）：主许可取自各包的 copyright
docker run --rm --entrypoint sh factorymind-ai-engine:0.1.0 \
    -c "grep -rh -m1 -E '^License:' /usr/share/doc/*/copyright | sed 's/^License: *//' | sort | uniq -c | sort -rn"

# AGPL 专项核查（命中项需逐条看上下文，勿只看文件名）
docker run --rm --entrypoint sh factorymind-ai-engine:0.1.0 \
    -c "grep -rl -E 'AGPL|Affero' /usr/share/doc/"

# 可选：更细粒度的 SBOM / 逐文件许可
trivy image --scanners license factorymind-ai-engine:0.1.0 factorymind-ai-web:0.1.0
syft factorymind-ai-engine:0.1.0 -o spdx-json > fm-engine.sbom.json
```

> 注：以上镜像内命令**不依赖任何外部扫描器**，只读镜像自带的包许可数据库，可离线复现。
> `pnpm audit` / `pip-audit` 需联网访问漏洞库；若 CI 环境不可用，请保留本页并注明"未在离线环境执行"。