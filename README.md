<div align="center">
  <h1>Agent Team</h1>
  <p><strong>把多个 AI 会话组织成会分工、会接班、会独立审核的长期团队。</strong></p>
  <p>
    <a href="https://github.com/AidenXu-1/agent-team-skill/releases/latest/download/agent-team-2.0-pure.zip"><strong>下载最新纯净版</strong></a>
    · <a href="#一分钟上手">一分钟上手</a>
    · <a href="SECURITY.md">安全边界</a>
  </p>
  <p>
    <a href="https://github.com/AidenXu-1/agent-team-skill/actions/workflows/ci.yml"><img src="https://github.com/AidenXu-1/agent-team-skill/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  </p>
</div>

> 当前公开发布版本为 `2.0.6`，当前源码构建为 `2.0.6`，项目内运行协议为 `1.4.10`。

Agent Team 是一个本地 Codex Skill。它用项目文件保存长期事实，让不同会话各司其职，即使更换会话也能继续工作。

## 它解决什么

| 常见问题 | Agent Team 的做法 |
|---|---|
| 一个会话太重，越做越乱 | 按长期职责分部门，每个会话只专注自己的工作 |
| 换会话后丢失上下文 | 用固定交接资料、唯一任务记录和按需查看的历史稳定接班 |
| 完成与验收混在一起 | 分开管理、执行和独立审核，验收不由执行者自己证明 |
| 技术回报看不懂 | 内部保留完整证据，用户默认只看“结果 / 需要你做什么 / 还需注意” |

## 一分钟上手

它适合长期、复杂、需要多个 AI 会话分工和独立验收的项目。启用后，会在你指定的项目内新增 `docs/collaboration/` 管理文件。Agent Team 自带脚本不会主动联网或上传项目内容；Codex 本身如何处理内容，以你使用的服务和隐私设置为准。

### 1. 让 AI 安装

把下面整句话发给 Codex：

```text
请下载并安装这个 Agent Team Skill 到 Codex 全局 Skill 目录，安装后验证文件完整性和版本，再告诉我结果：
https://github.com/AidenXu-1/agent-team-skill/releases/latest/download/agent-team-2.0-pure.zip
```

[查看 Latest Release](https://github.com/AidenXu-1/agent-team-skill/releases/latest) · [下载 SHA-256 校验文件](https://github.com/AidenXu-1/agent-team-skill/releases/latest/download/agent-team-2.0-pure.zip.sha256)

### 2. 开始使用

安装完成后，再把这句话发给 Codex：

```text
请使用 Agent Team Skill，判断这个项目最少需要哪些部门，并搭建可换会话、低上下文的协作团队。
```

Agent 会先判断最小必要团队，说清职责和会话模式，等你确认后再创建协作层。

<details>
<summary><strong>高级：手动安装</strong></summary>

下列命令会下载、解压，再把 5 个运行文件同步到 Codex 全局目录：

```bash
curl -L https://github.com/AidenXu-1/agent-team-skill/releases/latest/download/agent-team-2.0-pure.zip \
  -o agent-team-2.0-pure.zip
unzip -q agent-team-2.0-pure.zip -d agent-team-2.0-pure
cd agent-team-2.0-pure
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

</details>

## 运行逻辑

1. **先定团队**：根据交付物拆出最小必要的管理、执行和审核层。
2. **再建管理资料**：在项目内保存职责、任务、会话和必要的历史记录。
3. **一次只做一件事**：工具自动更新任务状态，不靠人工搬运或复制任务内容。
4. **独立核收**：执行部门提交产出与自检，审核部门亲自验证，统筹再向用户简短汇报。

### 核心保证

- 每个任务只有一份权威记录，不在多个文件之间复制状态。
- 新会话用固定交接资料接班，不重复读取无关的长历史。
- 产品体验、范围、发布、成本和隐私安全仍由用户确认。
- 会话变重时只建议换班，未获用户同意不自动更换。
- 临时外包只在用户主动提出时加载，并且只绑定一个任务。
- 任务完成、业务质量和用户授权分开记录，任何一项都不能冒充另一项。

<details>
<summary><strong>查看生成的协作结构</strong></summary>

> 下面是给维护者的技术结构。任务权威记录位于 `tasks/TASK-*.json`，完成状态收据为 `TASK_STATE_OK`。

```text
docs/collaboration/
├── 协议版本.json
├── README.md
├── 部门表.md
├── 路由表.md
├── 会话启动状态.json
├── tasks/TASK-*.json
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

</details>

## 运行与发布边界

全局安装目录只需要下列文件：

```text
SKILL.md
agents/openai.yaml
references/temporary-executor.md
scripts/scaffold_team.py
scripts/temporary_executor_runtime.py
```

`README.md`、`SECURITY.md`、`tests/semantic_review.md`、`scripts/verify_agent_team.py` 和 CI 只用于 GitHub 展示、开发与复验，不进入日常 Skill 运行目录。脚本的读写、Git 操作与隐私边界见 [SECURITY.md](SECURITY.md)。

`main` 是唯一公开主干。每次推送都会先运行完整 CI；只有全部通过，且该运行时文件提交仍是远端 `main` 的最新提交，才会更新 Latest 纯净包。验证或打包失败时，现有 Latest 包不会被替换。

自动验证构建使用与提交绑定的 `build-*` 标签；`v*` 版本标签和对应 Release 保留里程碑。当本地源码构建高于公开版时，表示修复尚未发布，不能把本地安装状态说成 GitHub Release 已更新。

<details>
<summary><strong>维护者验证与升级</strong></summary>

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m py_compile scripts/scaffold_team.py scripts/temporary_executor_runtime.py scripts/verify_agent_team.py
python3 scripts/verify_agent_team.py
python3 scripts/verify_agent_team.py --check-installed-copy "$HOME/.codex/skills/agent-team"
python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" .
```

涉及授权、职责或发布边界的语义变更，还要人工执行 [`tests/semantic_review.md`](tests/semantic_review.md)；自动测试通过不能替代语义审查。

升级已有协作层：

```bash
python3 scripts/scaffold_team.py "/path/to/project" --upgrade-collaboration
```

升级会先备份受管文件。遇到损坏 JSON、路径越界、符号链接或未登记的岗位说明修改时，会停止并保留现场，不猜测覆盖。

</details>

## License

[MIT License](LICENSE)
