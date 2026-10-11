# FactoryMind AI — 装配线质量 UNS 模型

> 基线：open supOS **V1.5.0.0-M**。本文件是 UNS 建模的说明性文档；机器可读的唯一事实来源是
> [`config/uns_model.json`](../services/ai-engine/config/uns_model.json)，导入脚本是
> [`scripts/seed_uns.py`](../services/ai-engine/scripts/seed_uns.py)，契约测试是
> [`tests/test_uns_model.py`](../services/ai-engine/tests/test_uns_model.py)。
> 端点与载荷字段均取自后端源码（`internal/interfaces/http/router.go`、`internal/domain/uns/types.go`），
> 未做任何猜测。

本模型描述一条**示范总装线**的质量数据组织方式，全部为合成/匿名标识，不含真实工厂、车型、
供应商或人员信息。它同时约束两件事：

1. **写侧**：`seed_uns.py` 按此模型向 supOS 批量创建节点；
2. **读侧**：`SupOsProvider` 按此模型约定的别名查询历史时序与当前值。

两侧必须完全一致，否则写入成功但读取为空——这正是 `tests/test_uns_model.py` 要钉住的接缝。

---

## 1. 层级结构

采用 ISA-95 风格的 `Factory → Line → Station → Equipment / Process` 五层，全部为
`pathType=0`（文件夹）节点：

```
FAC_DEMO_01                 示范总装工厂          factory
├── LINE_A                  总装一线              line
│   ├── ST_A10              内饰装配              station
│   ├── ST_A20              底盘合装              station
│   │   ├── EQ_A20_01       合装机器人            equipment (ROBOT)
│   │   └── PR_A20_01       底盘合装定位          process
│   ├── ST_A30              车门装配              station
│   ├── ST_A40              关键扭矩拧紧          station
│   │   ├── EQ_A40_01       电动拧紧枪 #1         equipment (TORQUE_TOOL)
│   │   ├── EQ_A40_02       电动拧紧枪 #2         equipment (TORQUE_TOOL)
│   │   ├── EQ_A40_03       工位 PLC              equipment (PLC)
│   │   ├── PR_A40_01       关键扭矩拧紧          process
│   │   ├── PR_A40_02       扭矩复检              process
│   │   ├── ST_A40_FPY      一次合格率            file (时序)
│   │   ├── ST_A40_NOK_RATE 不良率                file (时序)
│   │   ├── ST_A40_TORQUE_NOK_RATE  扭矩不合格率  file (时序)
│   │   ├── ST_A40_CYCLE_TIME       节拍          file (时序)
│   │   └── ST_A40_TOOL_ERROR_RATE  工具故障率    file (时序)
│   └── ST_A50              终检                  station
└── LINE_B                  总装二线              line
    ├── ST_B10 / ST_B20 / ST_B30 / ST_B40
```

规模：**2 条产线 / 9 个工位**，共 **64 个节点**（19 个文件夹 + 45 个时序文件）。
每个工位固定挂 **5 个**时序文件。

---

## 2. 别名规则（硬约束）

后端 `normalizeCreateFileCommand` 对 `alias` 施加正则
`^[A-Za-z_$][A-Za-z0-9_$]*$`，不合规即返回 `uns.alias.format.error`。
因此：

- 别名**只允许**字母、数字、下划线、`$`，且首字符不能是数字；
- **点号 `.` 与连字符 `-` 会被后端拒绝**，故原先的 `{station}.{metric}` 模板在真实实例上
  必然失败。现模板固定为下划线形式：

```
metricAliasTemplate = "{station}_{metric}"     例：ST_A40_FPY
```

指标 slug 与 `SupOsProvider.METRIC_ALIAS_SLUGS` 一一对应：

| MetricKey | 模型 slug | 单位 |
|---|---|---|
| `fpy` | `FPY` | % |
| `nokRate` | `NOK_RATE` | % |
| `torqueNokRate` | `TORQUE_NOK_RATE` | % |
| `cycleTime` | `CYCLE_TIME` | s |
| `toolErrorRate` | `TOOL_ERROR_RATE` | % |

> 该模板同时存在于 `app/config.py` 与 `app/providers/supos.py` 的
> `DEFAULT_METRIC_ALIAS_TEMPLATE`，`tests/test_uns_model.py::test_model_template_matches_provider`
> 断言三者一致。

---

## 3. 节点属性（`extendProperties`）

`GET /inter-api/supos/uns/tree` 返回的 `type` 实际映射 `pathType`（`0` 文件夹 / `2` 文件），
**无法区分文件夹是产线、工位还是设备**。因此每个文件夹节点在 `extendProperties` 中写入
自定义标记：

| 键 | 取值 | 说明 |
|---|---|---|
| `factorymindType` | `factory` / `line` / `station` / `equipment` / `process` | 适配器据此还原层级类型 |
| `equipmentType` | `ROBOT` / `TORQUE_TOOL` / `PLC` 等 | 仅设备节点；未知类型适配器**显式报错**而非静默 |
| `operation` | 文本 | 仅流程节点 |
| `country` | `CN` | 仅工厂根节点 |

适配器 `_normalize_node_type` 优先读取 `factorymindType`，缺失时才回退到 `pathType` 推断。

---

## 4. 时序文件节点

每个工位的每个指标是一个 `pathType=2` + `dataType=1`（时序）的文件节点：

| 字段 | 值 |
|---|---|
| `alias` | `{station}_{metric}`，如 `ST_A40_FPY` |
| `name` | `{工位名} {指标显示名}` |
| `displayName` | 指标显示名，如 `一次合格率 FPY` |
| `dataType` | `1`（时序） |
| `persistence` | `true`（落库持久化） |
| `definition` | 字段定义，见下 |

统一字段定义 `fileFields`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `value` | `DOUBLE` | 指标数值 |
| `timeStamp` | `DATETIME` | 采样时间 |
| `quality` | `LONG` | 数据质量码 |

读取路径：
- **历史时序**：`POST /inter-api/supos/uns/file/history/timeseries/query`（`alias` + 时间窗）；
- **当前值批量**：`POST /inter-api/supos/uns/file/current/batchQuery`（Body 为 alias 数组）。

---

## 5. 事件约定

**open supOS CE 没有任何 REST 事件查询端点**（源码中不存在 `/uns/event/query`），
事件只能经 **MQTT（EMQX）** 推送。因此适配器的事件源仅支持 `mqtt`，
`SUPOS_EVENT_SOURCE=rest` 会在启动时显式报错。

| 项 | 约定 |
|---|---|
| Broker 环境变量 | `SUPOS_MQTT_URL`（`mqtt://host:1883` 或 `mqtts://host`） |
| 发布主题 | `factorymind/uns/{station}/events`，如 `factorymind/uns/ST_A40/events` |
| 订阅主题 | `factorymind/uns/#` |
| 缓冲 | 有界时间窗缓冲（`MqttBackedSupOsClient`），支撑 `query_events` 的拉取式语义 |

事件载荷字段（与 `providers/supos_mqtt.parse_event_payload` 的解析键一致）：

```json
{
  "stationAlias": "ST_A40",
  "equipmentAlias": "EQ_A40_01",
  "type": "TOOL_ERROR",
  "severity": "HIGH",
  "message": "电动拧紧枪 #1 连续触发拧紧程序超差报警",
  "timestamp": "2026-10-01T09:30:00Z"
}
```

- `type` 取值同 `IndustrialEventType`：`MAINTENANCE` / `TOOL_ERROR` / `PLC_ERROR` /
  `SENSOR_FAULT` / `MATERIAL_CHANGE` / `SHIFT_CHANGE`；
- `severity` 取值同 `RiskLevel`：`LOW` / `MEDIUM` / `HIGH`；
- 载荷无法解析时**丢弃并记录日志**，绝不伪造事件。

---

## 6. 导入与校验

```bash
# 干跑：打印 payload 与摘要，不写实例（默认）
.venv/Scripts/python.exe scripts/seed_uns.py

# 真实导入（需实例与 access token；脚本以 supos_community_token Cookie 呈递）
.venv/Scripts/python.exe scripts/seed_uns.py --apply \
    --base-url http://<host>:18088 --token <access_token> --verify

# 契约测试：别名合法、父先子、每工位 5 文件、指标别名与 provider 一致、事件词表
.venv/Scripts/python.exe -m pytest tests/test_uns_model.py -q
```

导入调用 `POST /inter-api/supos/uns/batch`，Body 为 `[]BatchCreateTopicRequest`，
**按数组顺序创建**，父节点必须先于子节点出现（脚本在发送前校验并拒绝乱序）。
被 supOS 拒绝的节点会原样打印后端错误信息并以非零退出码结束，不做静默重试。

> **真机实测结论（2026-10-11，supOS CE `V1.5.0.0-M`）**
> - 已成功导入 **64 节点**（19 文件夹 + 45 时序文件）；`GET /uns/tree?parentId=1` 核验层级正确。
> - **`name` 有格式约束**：supOS 仅接受 `^[\p{Han}A-Za-z0-9_-]+$`（**不允许空格、`#`、`.` 等**）。
>   模型中的人类可读标签放在 `displayName`；`name` 由脚本用 `_safe_name()` 统一净化
>   （把非法字符替换为 `_`，超长截断至 63），避免 `uns.name.format.error`。
> - **父级字段用 `parentAlias`**（`BatchCreateTopicRequest` 同时有 `parentId`，本项目用别名）。
> - 鉴权用 `supos_community_token` Cookie（见 `SUPOS_INTEGRATION.md` §5），**不是** Bearer 头。

---

## 7. 与代码的对应关系

| 关注点 | 落点 |
|---|---|
| 模型定义 | `services/ai-engine/config/uns_model.json` |
| 导入脚本 | `services/ai-engine/scripts/seed_uns.py` |
| 契约测试 | `services/ai-engine/tests/test_uns_model.py` |
| 读侧适配器 | `services/ai-engine/app/providers/supos.py`（`SupOsProvider` / `HttpSupOsClient`） |
| 事件接入 | `services/ai-engine/app/providers/supos_mqtt.py`（`MqttBackedSupOsClient`） |
| 端点清单 | [`SUPOS_INTEGRATION_CHECKLIST.md`](./SUPOS_INTEGRATION_CHECKLIST.md) §4 |
| 集成指南 | [`SUPOS_INTEGRATION.md`](./SUPOS_INTEGRATION.md) |
