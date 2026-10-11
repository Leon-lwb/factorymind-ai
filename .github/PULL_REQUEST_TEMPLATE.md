## 变更内容

<!-- 一句话说清这个 PR 做了什么、为什么 -->

## 关联 Issue

Closes #

## 变更类型

- [ ] 新功能 (feat)
- [ ] 缺陷修复 (fix)
- [ ] 重构 (refactor)
- [ ] 文档 (docs)
- [ ] 工程/CI (chore)

## 自检清单

- [ ] 已通过 `bash scripts/verify.sh`（或手动跑通 ruff / pytest / typecheck / lint / build）
- [ ] 新增或修改的逻辑带有对应测试
- [ ] 未引入真实/敏感数据
- [ ] 若改动依赖，已更新 `docs/LICENSE_AUDIT.md`
- [ ] 若改动行为，已更新 `CHANGELOG.md`

## 架构红线确认

- [ ] 未让 LLM 参与确定性打分（RPN / AP / Z-score 等）
- [ ] 未让 LLM 编造 FMEA 记录（无匹配应返回 `NO_MATCH`）
- [ ] 未猜测 supOS API（如有集成改动，已在 `docs/SUPOS_RESEARCH.md` 记录来源）

## 测试方式

<!-- 复现/验证步骤，或附截图 -->

## 备注

<!-- 任何评审需要知道的取舍或遗留问题 -->