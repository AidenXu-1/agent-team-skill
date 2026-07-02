# 贡献指南

谢谢你愿意改进 `agent-team`。

这个仓库不是普通代码库，它是一个 Codex Skill。很多行为不是靠程序逻辑约束，而是靠 `SKILL.md` 和生成文档里的规则约束未来的 AI 会话。所以修改时要特别小心：一句话写得不清楚，可能会让后续 Agent 越过用户确认、误读职责边界，或把短唤醒当成任务全文。

## 修改原则

- 保持三层结构：管理层 / 执行层 / 审核层。
- 不要把所有项目默认当成软件项目。
- 不要移除用户确认闸。
- 不要让审核层继承执行层的结论。
- 不要让跨部门消息承载长上下文。
- 不要让设计节点只交文字说明。
- 不要让测试部在用户体验确认前介入。
- 新增或改动流程规则时，同步更新 `tests/pressure_scenarios.md` 和 `scripts/verify_agent_team.py`。

## 本地验证

提交前至少运行：

```bash
python3 scripts/verify_agent_team.py
```

推荐同时运行：

```bash
python3 /Users/aiden/.codex/skills/.system/skill-creator/scripts/quick_validate.py .
```

## Pull Request 检查清单

- [ ] 我运行过 `python3 scripts/verify_agent_team.py`。
- [ ] 如果改了行为规则，我更新了 `tests/pressure_scenarios.md`。
- [ ] 我没有提交 `.DS_Store`、`__pycache__`、`.env` 或私有项目资料。
- [ ] 我没有删除 Skill 运行所需文件：`SKILL.md`、`agents/`、`scripts/`、`tests/`。
- [ ] 我在说明里写清楚这次改的是文案、脚本、角色边界还是验证逻辑。
