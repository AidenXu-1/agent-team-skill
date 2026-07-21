# Agent Team 2.0 Skill

当前公开发布版本为 `2.0.6`，当前源码构建为 `2.0.6`，项目内运行协议为 `1.4.10`。`main` 是唯一公开主干，保存最新且已验证的源码；版本标签和正式 Release 保留里程碑；运行协议约束生成协作层的数据、工具与显式升级。本地源码构建高于公开版时，表示修复尚未发布，不能把本地安装状态说成 GitHub Release 已更新。

面向多 Agent / 多会话项目的轻量协作协议。它用项目文件保存长期真值，让会话可以安全接班，同时把管理、执行和独立审核分开。

## 核心设计

- 任务真值只有一份：`tasks/TASK-*.json`。状态变化不移动文件。
- 部门按长期决策权和交付边界划分，不按技术名词划分；产品部拥有需求、体验和系统级技术规划，开发部依据已确认合同完成代码与集成实现。
- 收件箱只是任务工具生成的活动索引，不保存任务正文。
- 交接班文档只写做到哪里、下一步、临时证据和已知坑，不保存任务状态。
- 新部门会话首次读四个入口文件；任务正文只在交接或收件箱指向时读取。
- Agent 按任务、权威性、遗漏风险和当前能力决定项目正文的读取范围。Skill 不规定全文、局部读取或特定检索工具。
- 机械且需要一致性的操作交给生成脚本：任务状态、会话状态、事实日志和按需启用的单 TASK 临时外包。
- 用户主动发起时，可创建绑定一个 TASK 和父部门的临时外包；当前完整运行适配只开放临时开发外包，其他父部门先使用通用 preflight，不能冒充已经具备专业交付链。
- 会话变重只提醒用户是否换班；用户明确授权后才执行。
- 设计意图预览按需触发。触发后必须让用户直接看到，并说明与最终实现的差距。
- 普通任务不强制写正式报告或日志；共享报告格式只保留一份。
- 审核层任务例外：必须提交带任务一致 YAML 的本部门审核报告；普通部门不能误用核收命令。
- `TASK_STATE_OK` 是事务收据，不是业务质量证明。领取人、核收人、授权证据和会话证据都是审计声明，不构成身份认证。
- 部门完成四件套保留在内部；统筹面向非程序员用户默认只报“结果 / 需要你做什么 / 还需注意”，只在拍板、体验、重要变化/风险和节点完成时打扰用户。

## 运行边界

全局运行目录只需要：

```text
SKILL.md
agents/openai.yaml
references/temporary-executor.md
scripts/scaffold_team.py
scripts/temporary_executor_runtime.py
```

仓库里的 `README.md`、`tests/semantic_review.md`、`scripts/verify_agent_team.py` 和 CI 用于开发与复验，不参与日常 Skill 注入。

## 安装

公开稳定版从 [Latest Release](https://github.com/AidenXu-1/agent-team-skill/releases/latest) 获取。每次 `main` 推送都先运行完整 CI；只有全部通过，且该提交仍是远端 `main` 的最新提交，才会自动更新 [固定最新纯净包](https://github.com/AidenXu-1/agent-team-skill/releases/latest/download/agent-team-2.0-pure.zip) 和 [SHA-256 校验文件](https://github.com/AidenXu-1/agent-team-skill/releases/latest/download/agent-team-2.0-pure.zip.sha256)；源码或打包验证在发布前失败时，现有 Latest 包不会被替换。以下命令从当前已发布的 `2.0.6` 源码检出安装；本地安装副本、`main` 与公开 Latest 纯净包应保持五个运行文件逐字节一致：

```bash
mkdir -p ~/.codex/skills/agent-team
rsync -a --delete --delete-excluded \
  --include='/SKILL.md' \
  --include='/agents/' --include='/agents/openai.yaml' \
  --include='/references/' --include='/references/temporary-executor.md' \
  --include='/scripts/' --include='/scripts/scaffold_team.py' \
  --include='/scripts/temporary_executor_runtime.py' \
  --exclude='*' \
  ./ ~/.codex/skills/agent-team/
```

日常开发使用短期分支，通过审查和 CI 后合并到 `main`。自动验证构建使用与源码提交绑定的 `build-*` 标签；`v*` 版本标签和对应 Release 作为不变的里程碑档案。

## 使用

对 Agent 说：

```text
请使用 Agent Team Skill，判断这个项目最少需要哪些部门，并搭建可换会话、低上下文的协作团队。
```

或直接运行：

```bash
python3 scripts/scaffold_team.py "/path/to/project" \
  --profile "通用项目协作" \
  --roles "lead,do,review" \
  --session-mode manual \
  --foundation-file docs/spec.md
```

Agent 必须先阅读地基并确认它已说清目标、交付范围和验收标准，再通过 `--foundation-file` 显式声明已复核的项目内文件。脚本只负责机械路径和文件安全检查，不评判文本语义。使用 `docs/spec.md` 之外的地基时需另加 `--allow-without-foundation`。用户确认缺少适用地基且允许创建通用最小地基时，才改用 `--allow-without-foundation --create-minimal-foundation` 及六项地基参数。

临时外包是按需旁路。只有用户主动提出时，Agent 才读取 [`references/temporary-executor.md`](references/temporary-executor.md)；普通团队搭建不加载这份冷规则。

## 生成结构

```text
docs/collaboration/
├── 协议版本.json
├── README.md
├── 部门表.md
├── 路由表.md
├── 会话启动清单.md
├── 会话启动状态.json
├── 任务交接模板.md
├── 错题集.md
├── tasks/TASK-*.json
├── 模板/
│   ├── 工作报告.md
│   ├── 审核报告.md
│   └── 专项结论.md
├── scripts/
│   ├── agent_team_task.py
│   ├── agent_team_session.py
│   ├── agent_team_log.py
│   └── agent_team_temporary.py
└── 部门/<部门>/
    ├── 上岗引导.md
    ├── 岗位说明.md
    ├── 交接班文档.md
    ├── 收件箱.md
    ├── 报告/
    └── 日志/
```

日志周文件在第一条真实事件写入时创建。每个部门不再生成重复的报告说明文件。

源码 `2.0.6` 的任务工具把派单和授权绑定当前统筹会话；审核报告只有 `final + pass/fail` 和真实摘要才能完成。用户拒绝或放弃的普通任务用 `resolve` 保留证据收口，其中 `rejected_by_user` 会同步授权拒绝轴。无需全历史判断的热路径不再被一份冷历史 TASK 冻结，索引会提示陈旧；并发判断仍安全停止，`agent_team_task.py doctor` 负责完整历史体检。`acknowledged` 的中文展示为“统筹已核收”，不再误写成“已归档”。

## 用户常用口令

- `接班`：读取本部门入口并恢复当前状态；有明确任务时可以继续执行。
- `先接班，不要开始任务`：只恢复职责、状态和待办，汇报后停下。
- `交班`：更新交接班文档和必要事实日志，不更换会话。
- `换班`、`换会话`：授权同部门创建全新会话接班；新会话登记成功后才归档旧会话。

## 验证

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m py_compile scripts/scaffold_team.py scripts/temporary_executor_runtime.py scripts/verify_agent_team.py
python3 scripts/verify_agent_team.py
python3 scripts/verify_agent_team.py --check-installed-copy "$HOME/.codex/skills/agent-team"
python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" .
```

涉及授权、职责或发布边界的语义变更，还要人工执行 [`tests/semantic_review.md`](tests/semantic_review.md)；自动测试通过不能替代这一步。

协议升级：

```bash
python3 scripts/scaffold_team.py "/path/to/project" --upgrade-collaboration
```

安全停用一个空闲部门，并保留全部历史：

```bash
python3 /path/to/project/docs/collaboration/scripts/agent_team_session.py retire \
  --department 研究部 --actor "统筹部/已登记会话ID" \
  --evidence "host=<工具> thread_id=<研究部当前ID> archived=true"

python3 scripts/scaffold_team.py "/path/to/project" \
  --deactivate-roles "research" \
  --deactivation-evidence "用户确认消息或会话指针"
```

部门表丢失或损坏时，从会话状态真值重建：

```bash
python3 /path/to/project/docs/collaboration/scripts/agent_team_session.py rebuild-registry
```

升级会备份受管文件，并把旧版按状态分目录的 TASK JSON 迁移为稳定的平铺路径。遇到损坏 JSON、路径越界或符号链接时先停止，不猜测修复。若任一 `岗位说明.md` 相对上次受管清单有变化，升级默认停止，避免模板静默缩窄项目定制职责；用户确认要保留这些项目覆盖后，再运行：

```bash
python3 scripts/scaffold_team.py "/path/to/project" \
  --upgrade-collaboration --role-policy-overlay-file "/safe/path/role-overlays.json"
```

追加层 JSON 只允许 `additions`，每项需有稳定 ID、所属职责段、项目补充文本和用户确认依据。脚本在 `协议版本.json` 的 `role_policy_overlays` 中登记，每次用当前标准模板重建岗位说明，再追加项目补充。直接编辑受管岗位说明仍会停止升级。

## License

MIT License. See [LICENSE](LICENSE).
