# Agent Team Skill

把一个 AI 会话拆成一套“多部门协作系统”的 Codex Skill。

它适合在项目变复杂以后使用：一个会话负责统筹，一个或多个会话负责执行，再用独立会话做审核把关。每个会话都有自己的职责边界、收件箱、交接班文档、日志和确认闸，避免 AI 在长上下文里越权、乱推进、忘记交接。

## 这个仓库是什么

这是一个可以安装到 Codex 的 Skill 仓库。

真正参与 Skill 运行的是这些文件：

```text
SKILL.md
agents/openai.yaml
scripts/scaffold_team.py
scripts/verify_agent_team.py
tests/pressure_scenarios.md
```

其他文件，比如 `README.md`、`LICENSE`、`.github/`，是为了 GitHub 开源展示、协作和自动验证准备的，不是 Skill 运行时必须读取的内容。

## 它解决什么问题

当一个项目只靠一个 AI 会话推进时，很容易出现这些问题：

- 做着做着忘记上一轮边界。
- 执行、审核、统筹混在一起。
- 没有明确“谁负责、谁审核、谁拍板”。
- AI 把“建议下一步”误当成用户已经同意。
- 测试、安全、成本、设计确认被跳过。
- 多个会话之间靠复制大段上下文沟通，越聊越乱。

`agent-team` 的做法是：把多会话组织成“部门制”。

它会在项目里生成 `docs/collaboration/` 协作层，把部门职责、收件箱、交接班、日志、审核报告和用户确认节点落到文件里。

## 核心机制

- **三层架构**：管理层 / 执行层 / 审核层必须齐全。
- **收件箱是真相源**：任务详情写文件，不靠聊天窗口口头传递。
- **读取路由器**：新会话先运行生成的 `agent_team_read.py`，只返回本部门必读文件和报告 YAML 摘要，避免默认扫全局长文。
- **短唤醒**：跨会话通知只说“有新任务 / 任务已完成 / 遇到阻断”。
- **节点式推进**：一个功能或环节只推进一个验收节点。
- **完成回报四件套**：产出路径、验证结果、日志收据、错题自检。
- **用户确认闸**：产品体验、功能取舍、UI/视觉、上线、外发、成本、安全等重大节点必须由用户拍板。
- **审核层独立**：测试、安全、财务等审核部门亲自验证，不采信执行部门转述。
- **报告元数据**：报告、审核报告、专项结论、关键决策统一使用 YAML frontmatter，脚本只读人工预写的 `summary`，不创造性总结正文。
- **体验先于测试**：可运行功能先给用户体验，再进入专业测试。
- **设计意图必须可视化**：涉及 UI/交互/视觉时，不能只交文字说明；但设计预览不得声称等同真实 App UI，最终 UI 验收以运行中的 App / 真实路由 / 构建或打包态截图为准。

## 安装到 Codex

如果你希望 Codex 自动发现这个 Skill，可以克隆到 `~/.codex/skills/agent-team`：

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/AidenXu-1/agent-team-skill.git ~/.codex/skills/agent-team
```

如果你已经有本地副本，也可以用同步方式：

```bash
rsync -a --delete ./ ~/.codex/skills/agent-team/ \
  --exclude .git \
  --exclude .github \
  --exclude README.md \
  --exclude LICENSE \
  --exclude CHANGELOG.md \
  --exclude CONTRIBUTING.md \
  --exclude SECURITY.md
```

安装后，如果当前 Codex 环境不会自动热加载新 Skill，请重启或刷新 Codex。

## 如何触发

你可以对 Codex 说类似这样的话：

```text
帮我给这个项目搭建一个多部门协作团队。
```

或：

```text
这个项目需要多个 Agent 分工协作，帮我诊断应该有哪些部门。
```

Skill 触发后，不应该直接默认软件项目，也不应该立刻创建部门。它会先确认项目最终交付物和会话创建模式，再推荐部门配置。

## 直接运行脚本

最小通用团队：

```bash
python3 scripts/scaffold_team.py "/path/to/project" \
  --profile "通用项目协作" \
  --roles "lead,do,review" \
  --session-mode "manual"
```

软件项目团队：

```bash
python3 scripts/scaffold_team.py "/path/to/project" \
  --profile "软件产品 + UI + 质量关" \
  --roles "lead,product,design,dev,test" \
  --session-mode "manual"
```

非软件项目，并由 Skill 补一个最小业务地基：

```bash
python3 scripts/scaffold_team.py "/path/to/project" \
  --profile "课程交付项目" \
  --roles "lead,research,planning,do,review" \
  --session-mode "manual" \
  --allow-without-foundation \
  --create-minimal-foundation
```

## 验证

修改 Skill 或脚本后，运行：

```bash
python3 scripts/verify_agent_team.py
```

也可以运行官方 Skill 基础校验：

```bash
python3 /Users/aiden/.codex/skills/.system/skill-creator/scripts/quick_validate.py .
```

期望结果：

```text
Skill is valid!
PASS: agent-team generated docs preserve node-gate protocol
```

## 仓库结构

```text
.
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   ├── scaffold_team.py
│   └── verify_agent_team.py
├── tests/
│   └── pressure_scenarios.md
├── README.md
├── LICENSE
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
└── .github/
```

## 适合哪些项目

- 软件产品 / Vibe Coding 项目
- 内容、课程、训练营、交付型项目
- 咨询、调研、方案、运营项目
- 数据处理、自动化流程、跨平台执行项目
- 任何需要多会话分工、交接、审核的项目

## 不适合什么情况

- 只需要一次性回答的小问题。
- 没有必要拆分职责的简单任务。
- 用户还没确认项目最终交付物，却想直接生成一堆部门。
- 希望 AI 跳过用户确认，自动决定上线、发布、付费、授权或高风险操作。

## License

MIT License. See [LICENSE](LICENSE).
