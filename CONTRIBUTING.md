# Contributing to FactoryMind AI

感谢参与。本文说明如何搭建环境、提交变更与通过检查。**请在动手前先读 [README](README.md) 与 [数据安全](#数据安全) 一节。**

---

## 1. 开发环境

- Python **3.11+**、Node **18+**、pnpm **9+**

```bash
# 后端
cd services/ai-engine
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# 前端（仓库根目录）
pnpm install
```

---

## 2. 提交前必须通过的检查

```bash
# 后端
cd services/ai-engine
python -m ruff check app tests scripts
python -m pytest -q

# 前端（仓库根目录）
pnpm typecheck
pnpm lint
pnpm build
```

一键脚本（CI 不可用时的本地等价物）：

```bash
bash scripts/verify.sh
```

---

## 3. 分支与提交

- 从 `main` 拉出特性分支：`feat/xxx`、`fix/xxx`、`docs/xxx`。
- 提交信息采用约定式前缀，一句话说清**为什么**：
  ```
  feat(engine): 根因排序加入历史证据项
  fix(fmea): 规范化匹配忽略尾部标点
  docs(readme): 补充 supOS 双模式说明
  ```
- 一次提交只做一件事；避免把格式化与逻辑改动混在一起。

---

## 4. 代码约定

- **后端**：Python 3.11 类型标注；`ruff` 行宽 100；配置（阈值/权重/规则）放 `config/*.json`，**不要硬编码进代码**。
- **前端**：TypeScript 严格模式；组件放 `apps/web/src/`；UI 保持**深色工业控制塔**风格。
- **注释**：只写"为什么"，不写"是什么"；不写复述代码的注释。
- **测试**：新增确定性逻辑必须带单测；修复缺陷应附回归测试。

---

## 5. 架构红线（评审会看）

1. **确定性计算与 AI 分离**：FPY/NOK/节拍/Z-score/RPN/AP 由确定性代码计算；LLM 只解释、不打分。
2. **AI 输出必须可溯源**：包含 `evidence / confidence / reasoning / source / timestamp`，禁止"一定是根因"式断言。
3. **FMEA 不编造**：找不到匹配返回 `NO_MATCH` 并转人工；禁止 LLM 臆造历史 FMEA 记录。
4. **双 Provider 抽象**：`AIProvider` 与 `IndustrialDataProvider` 通过环境变量切换；**Mock 模式必须能完整跑通全闭环**。
5. **不猜测 supOS API**：集成前先读官方仓库/文档/当前部署版本，并在 `docs/SUPOS_RESEARCH.md` 记录来源。
6. **不做**（规格书 §52）：多 Agent swarm、自研大模型/RAG、K8s、真实 MES/PLC 接入、多租户、复杂数字孪生。

---

## 6. 数据安全

- **严禁**提交公司内部文档、真实生产数据、车型信息、内部 FMEA 原件、账号密钥。
- 仓库内一切数据必须为**合成/匿名/公开**数据。
- 真实凭证只经环境变量注入；`.env` 已由 `.gitignore` 排除。

---

## 7. 许可证

- 本项目采用 **Apache-2.0**。提交即表示你同意以该许可授权你的贡献。
- 引入新依赖前，先核对许可证并更新 [`docs/LICENSE_AUDIT.md`](docs/LICENSE_AUDIT.md)；**禁止引入 GPL/AGPL/SSPL 等强传染许可**，尤其**禁止直接集成 MinIO SDK（AGPL-3.0）**。

---

## 8. Pull Request

提 PR 时请使用仓库的 PR 模板，并确保：

- [ ] 已通过 §2 的全部检查
- [ ] 新增/修改逻辑有对应测试
- [ ] 未引入真实/敏感数据
- [ ] 若改了依赖，已更新 `docs/LICENSE_AUDIT.md`
- [ ] 若改了行为，已在 `CHANGELOG.md` 记录