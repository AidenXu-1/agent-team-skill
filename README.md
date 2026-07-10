# Agent Team Skill

面向互联网 AI 产品开发的多会话部门协作协议与脚手架,同时兼容内容、运营、调研等非软件团队。

主要组织产品、设计、开发、AI 工程/评测、测试、安全和成本节点。每个会话都有职责边界、收件箱、交接班和审核闸。已知文件用确定性搜索/切片;跨文档且遗漏代价高时,用“AI 加法式扩词 + 脚本高召回候选 + 有界证据包 + 覆盖报告”,避免整篇通读,也避免为了省 token 静默漏证据。

## 这个仓库是什么

这是一个面向多 Agent / 多会话协作的项目脚手架仓库。它可以作为全局 Skill / 工具安装使用，也可以由任何支持本地文件读写和 Python 脚本执行的 AI Agent 直接调用。

GitHub 页面里的 `README.md` 和 `LICENSE` 是展示说明与开源许可；真正参与协作层生成的是这 4 个文件：

```text
SKILL.md
agents/openai.yaml
scripts/scaffold_team.py
scripts/agent_team_research.py
```

源码仓库保留验证脚本、压力场景和 CI,保证每次修改可回归;全局安装时仍只同步上面四项运行内容,不把开发材料载入 Skill 运行上下文。

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
- **接班包**：`onboard` 直接返回岗位核心、交接摘要和最新待办,Agent 不再按文件路径全文通读。
- **两级检索**：已知文件/关键词用 `find/search/slice`;跨文档、术语未知或高遗漏风险才用研究路由器。单次硬输出预算负责防失控,不冒充“证据已经完整”。
- **召回不被 AI 静默裁掉**：原问题和精确命中永久保留;AI 只做加法式扩词、分组和重排,完整候选留在 manifest,并用 coverage 暴露未扫描范围与截断。
- **短唤醒**：跨会话通知只说“有新任务 / 任务已完成 / 遇到阻断”。
- **节点式推进**：一个功能或环节只推进一个验收节点。
- **完成回报四件套**：产出路径、验证结果、日志收据、错题自检。
- **用户确认闸**：产品体验、功能取舍、UI/视觉、上线、外发、成本、安全等重大节点必须由用户拍板。
- **审核层独立**：测试、安全、财务等审核部门亲自验证，不采信执行部门转述。
- **报告元数据**：使用受限单行元数据;重复键、未知字段、多行值和超长值直接拒绝,不对正文做创造性总结。
- **AI 工程部**：当模型/API/Prompt/RAG/Agent/评测集/推理成本是独立核心链路时启用 `ai`,不为了“有 AI”就强拆部门。
- **体验先于测试**：可运行功能先给用户体验，再进入专业测试。
- **设计意图必须可视化**：涉及 UI/交互/视觉时，不能只交文字说明；但设计预览不得声称等同真实 App UI，最终 UI 验收以运行中的 App / 真实路由 / 构建或打包态截图为准。

## 使用方式

### 1. 仓库地址

```text
https://github.com/AidenXu-1/agent-team-skill
```

### 2. 让 Agent 全局安装

把下面这段话复制给你的 Agent，让它按自己的 Skill / 工具目录规则安装：

```text
请把这个仓库安装成全局可用的 Agent Team Skill：

https://github.com/AidenXu-1/agent-team-skill

安装要求：
1. 克隆或下载仓库。
2. 只把运行所需文件放进全局 Skill 目录：
   - SKILL.md
   - agents/openai.yaml
   - scripts/scaffold_team.py
   - scripts/agent_team_research.py
3. 不要把 README.md、LICENSE、.git 或其他说明 / 开发材料放进运行目录。
4. 安装后确认全局运行目录至少包含以上四项，并能读取 SKILL.md、执行两个 Python 运行脚本。
```

如果你的 Agent 使用 `~/.codex/skills/` 作为全局 Skill 目录，可以执行：

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/AidenXu-1/agent-team-skill.git /tmp/agent-team-skill
mkdir -p ~/.codex/skills/agent-team
rsync -a --delete --delete-excluded \
  --include='/SKILL.md' \
  --include='/agents/' --include='/agents/openai.yaml' \
  --include='/scripts/' --include='/scripts/scaffold_team.py' --include='/scripts/agent_team_research.py' \
  --exclude='*' \
  /tmp/agent-team-skill/ ~/.codex/skills/agent-team/
```

如果你已经有本地仓库副本，也可以从仓库根目录同步：

```bash
mkdir -p ~/.codex/skills/agent-team
rsync -a --delete --delete-excluded \
  --include='/SKILL.md' \
  --include='/agents/' --include='/agents/openai.yaml' \
  --include='/scripts/' --include='/scripts/scaffold_team.py' --include='/scripts/agent_team_research.py' \
  --exclude='*' \
  ./ ~/.codex/skills/agent-team/
```

如果当前 Agent 环境不会自动热加载新 Skill，请重启、刷新，或开启一个新会话。

### 3. 安装后怎么用

在目标项目里，对 Agent 说：

```text
请使用 Agent Team Skill，帮我判断这个项目需要哪些部门，并搭建多会话协作团队。
```

Agent 应先阅读 `SKILL.md`，确认项目最终交付物和会话创建模式，再决定部门配置。不要直接默认软件项目，也不要立刻创建部门。

也可以直接运行脚本：

```bash
python3 scripts/scaffold_team.py "/path/to/project" \
  --profile "通用项目协作" \
  --roles "lead,do,review" \
  --session-mode "manual"
```

脚本会在目标项目中生成 `docs/collaboration/`，包括部门表、会话启动清单、岗位说明、上岗引导、收件箱、交接班文档、日志目录、审核报告目录和读取路由脚本。

## 如何触发

安装后，可以直接用自然语言触发：

```text
帮我给这个项目搭建一个多部门协作团队。
```

或：

```text
这个项目需要多个 Agent 分工协作，帮我诊断应该有哪些部门。
```

在人工流程中，也可以先让 Agent 阅读 `SKILL.md`，再由它根据项目类型选择角色并调用 `scripts/scaffold_team.py`。

无论在哪种环境中使用，都不应该直接默认软件项目，也不应该立刻创建部门。它应先确认项目最终交付物和会话创建模式，再推荐部门配置。

## 直接运行脚本

最小通用团队：

```bash
python3 scripts/scaffold_team.py "/path/to/project" \
  --profile "通用项目协作" \
  --roles "lead,do,review" \
  --session-mode "manual"
```

互联网 AI 产品团队：

```bash
python3 scripts/scaffold_team.py "/path/to/project" \
  --profile "互联网 AI 产品 + UI + 模型评测 + 质量关" \
  --roles "lead,product,design,dev,ai,test" \
  --session-mode "manual"
```

从长文中只取相关内容：

```bash
python3 docs/collaboration/scripts/agent_team_read.py search path/to/article.md \
  --query "用户可见错误" --context 2 --limit 20
python3 docs/collaboration/scripts/agent_team_read.py slice path/to/article.md \
  --start-line 120 --end-line 170
```

非软件项目，并由 Skill 补一个最小业务地基：

```bash
python3 scripts/scaffold_team.py "/path/to/project" \
  --profile "课程交付项目" \
  --roles "lead,research,planning,do,review" \
  --session-mode "manual" \
  --allow-without-foundation \
  --create-minimal-foundation \
  --foundation-goal "为新员工提供可复用的入职课程" \
  --foundation-deliverable "课程大纲、讲义和练习题" \
  --foundation-audience "入职 30 天内的运营人员" \
  --foundation-acceptance "试学者完成练习且关键题正确率达到 80%" \
  --foundation-resources "现有 SOP、访谈记录和两名业务专家" \
  --foundation-risks "术语过时、案例失真;由业务专家复核"
```

跨文档且不能漏证据的研究任务：

```bash
python3 docs/collaboration/scripts/agent_team_research.py candidates \
  --task-id auth-risk --query "登录稳定性风险" \
  --expand "鉴权失败" --expand "令牌过期" --expand "反例" --path docs
python3 docs/collaboration/scripts/agent_team_research.py pack \
  --task-id auth-risk --ids <候选ID1> <候选ID2> --target-tokens 6000
python3 docs/collaboration/scripts/agent_team_research.py coverage --task-id auth-risk
```

每个 `--expand` 只放一个短词或短语,不要把多个概念拼成长句。`target-tokens` 是可调软目标,复杂任务可以调高或拆多个证据包;它不是完成条件。第一版刻意不引入向量数据库,先用中文安全的词法特征、逐查询配额和融合排序建立可复现基线。覆盖仍不足时最多补一轮限制条件/失败模式/反证词检索,再不足就列为未验证项或拆任务。

## 仓库结构

```text
.
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   ├── scaffold_team.py
│   ├── agent_team_research.py
│   └── verify_agent_team.py
├── tests/
│   └── pressure_scenarios.md
├── docs/rules/
│   └── agent-team-reading-router-design.md
├── .github/workflows/ci.yml
├── README.md
└── LICENSE
```

> 注：源码仓库包含验证脚本、压力场景、设计留档和 CI；全局安装规则会排除它们,只保留运行文件。

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
