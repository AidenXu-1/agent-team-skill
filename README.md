# Agent Team 2.0 Skill

当前公开发布版本为 `2.0.2`，当前源码构建为 `2.0.2`，项目内运行协议为 `1.4.6`。公开版本对应 Git 标签和 Release；源码构建标识当前代码；运行协议约束生成协作层的数据、工具与显式升级。第一版保留在 `main` 分支；2.0 通过独立标签发布。

面向多 Agent / 多会话项目的轻量协作协议。它用项目文件保存长期真值，让会话可以安全接班，同时把管理、执行和独立审核分开。

## 核心设计

- 任务真值只有一份：`tasks/TASK-*.json`。状态变化不移动文件。
- 部门按长期决策权和交付边界划分，不按技术名词划分；AI 产品仍由产品部规划、开发部完整实现。
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

公开稳定版从 [Latest Release](https://github.com/AidenXu-1/agent-team-skill/releases/latest) 获取。[固定最新纯净包](https://github.com/AidenXu-1/agent-team-skill/releases/latest/download/agent-team-2.0-pure.zip) 始终指向最新正式版，对应的 [SHA-256 校验文件](https://github.com/AidenXu-1/agent-team-skill/releases/latest/download/agent-team-2.0-pure.zip.sha256) 使用同一固定地址。以下命令从当前 `2.0.2` 源码检出安装，五个运行文件应与正式纯净包逐字节一致：

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

升级会备份受管文件，并把旧版按状态分目录的 TASK JSON 迁移为稳定的平铺路径。遇到损坏 JSON、路径越界或符号链接时先停止，不猜测修复。

## License

MIT License. See [LICENSE](LICENSE).
