<div align="center">
  <h1>Agent Team</h1>
  <p><strong>把多个 AI 会话组织成会分工、会接班、会独立审核的长期团队。</strong></p>
  <p>
    <a href="https://github.com/AidenXu-1/agent-team-skill/releases"><strong>查看已发布版本</strong></a>
    · <a href="#一分钟上手">一分钟上手</a>
    · <a href="SECURITY.md">安全边界</a>
  </p>
  <p>
    <a href="https://github.com/AidenXu-1/agent-team-skill/actions/workflows/ci.yml"><img src="https://github.com/AidenXu-1/agent-team-skill/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  </p>
</div>

> 当前公开发布版、当前源码候选、本机全局安装副本、项目生成协议是四份独立真值。此处源码候选为 `2.1.0`、生成协议为 `1.5.0`；候选尚未等于公开 Release，本机安装版也必须另行核验。候选唯一内容身份见 `candidate-manifest.json`。

Agent Team 是一个本地 Codex Skill。它用项目文件保存长期事实，让不同会话各司其职，即使更换会话也能继续工作。

## 它解决什么

| 常见问题 | Agent Team 的做法 |
|---|---|
| 一个会话太重，越做越乱 | 按长期职责分部门，每个会话只专注自己的工作 |
| 换会话后丢失上下文 | 用带 freshness 的固定交接、登记身份和活动索引稳定接班 |
| 任务越返工越多 | 同一时刻只保留一个切片、一个 owner 和一个当前候选 |
| 热上下文随历史膨胀 | 热入口只显示当前切片，完整 TASK 与日志留在冷历史 |
| 完成与验收混在一起 | 分开管理、执行和独立审核，验收不由执行者自己证明 |
| 技术回报看不懂 | 内部保留完整证据，用户默认只看“结果 / 需要你做什么 / 还需注意” |

## 一分钟上手

它适合长期、复杂、需要多个 AI 会话分工和独立验收的项目。启用后，会在你指定的项目内新增 `docs/collaboration/` 管理文件。Agent Team 自带脚本不会主动联网或上传项目内容；Codex 本身如何处理内容，以你使用的服务和隐私设置为准。

### 1. 让 AI 安装

把下面整句话发给 Codex：

```text
请打开下面的 Agent Team Releases 页面，只选择标记为 Latest 的正式 Release，下载其中同版本的 agent-team-X.Y.Z-pure.zip 和 .sha256；校验一致后安装到 Codex 全局 Skill 目录，再告诉我实际安装版本：
https://github.com/AidenXu-1/agent-team-skill/releases
```

[查看正式 Releases](https://github.com/AidenXu-1/agent-team-skill/releases)

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
VERSION="<在 Releases 页面确认的 Latest 版本，如 2.1.0>"
curl -L "https://github.com/AidenXu-1/agent-team-skill/releases/download/v${VERSION}/agent-team-${VERSION}-pure.zip" \
  -o "agent-team-${VERSION}-pure.zip"
curl -L "https://github.com/AidenXu-1/agent-team-skill/releases/download/v${VERSION}/agent-team-${VERSION}-pure.zip.sha256" \
  -o "agent-team-${VERSION}-pure.zip.sha256"
shasum -a 256 -c "agent-team-${VERSION}-pure.zip.sha256"
unzip -q "agent-team-${VERSION}-pure.zip" -d "agent-team-${VERSION}-pure"
cd "agent-team-${VERSION}-pure"
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
3. **一次只跑一个切片**：一个 owner、最多两个审核 gate、一个带哈希的当前候选；返工更新候选代次，不另开 replacement owner。
4. **独立核收**：gate 必须审核同一候选并验证真实出口，同一 gate 跨两代连续失败会自动冻结新工作。

### 核心保证

- 普通任务动作绑定部门当前已登记会话；换班后先留下 ownership rebind 记录。
- 新会话读取上岗引导后只运行一次 `onboard-bundle`；它只从活动切片和受管 legacy 窄索引确定 TASK 身份，不从人工交接文字猜任务。冻结恢复任务会单独标出；热内容超过 24,000 字节时告警但不截断。
- 冷历史仍保留在磁盘供审计；本版本减少日常读取，不承诺自动删除历史或降低物理存储占用。
- 产品体验、范围、发布、成本和隐私安全仍由用户确认。
- 会话变重时只建议换班，未获用户同意不自动更换。
- 正常模式只推进一个端到端切片；出现用户冻结、任务堆积、上下文/存储压力或同一 gate 跨两代连续失败时，先机械冻结新派单，再清账、交接和复盘根因。
- 没有宿主 heartbeat、lease、恢复与归档适配器时明确为 `manual-degraded`，不轮询、不承诺无人值守；接班 A/B 不能外推成完整项目生命周期的 Token 降幅。
- 协议 1.5 暂不另开临时外包 TASK，避免绕过单 owner；等待 P2 宿主适配器。
- 任务完成、业务质量和用户授权分开记录，任何一项都不能冒充另一项。

### 真实接班 Token A/B

在同一 `gpt-5.6-sol`、同一提示和 927 条合成冷历史 TASK 下，按“候选、旧版、旧版、候选”交替实测两次：2.0.11 平均用了 5.5 次工具调用和 123,991.5 输入 Token；2.1 候选平均用了 2 次调用和 81,865 输入 Token，观察到输入下降 34.0%，两组配对降幅分别为 25.3% 和 40.8%。候选实际热文件从 5,703 增至 6,257 字节，因此下降来自减少宿主往返，并非删规则或少读当前 TASK。

这仍是每侧仅两次的工程探针，不代表 Lulu 业务全流程、长期会话或计费成本必然同比下降。汇总、原始 JSONL 摘录和可复建夹具分别见 [`tests/token-ab-20260826.json`](tests/token-ab-20260826.json)、[`tests/token-ab-receipts-20260826.jsonl`](tests/token-ab-receipts-20260826.jsonl) 和 [`tests/build_token_ab_fixture.py`](tests/build_token_ab_fixture.py)。

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

`main` 是唯一公开主干。push 和 pull request 只运行验证，不创建 Release。公开发布必须由维护者手动触发，并同时提交被独立审查的 commit SHA、运行集 SHA-256、精确版本和 `PUBLISH v<版本>` 确认；缺任一项都失败关闭。

仓库已固化由五文件 SHA-256 清单约束的 `tests/fixtures/agent-team-2.0.11-runtime`；普通验证和发布任务都必须完成真实 2.0.11 迁移。当前候选仍未提交、未完成 GitHub CI，因此不得发布。

正式包使用不可复用的 `v2.1.0` 版本标签和 `agent-team-2.1.0-pure.zip` 文件名。发布前还要把 `candidate-manifest.json` 标记为 `reviewed-release-candidate`；其中 `base_commit` 始终表示本轮开发起点，正式候选 commit 由发布输入单独绑定，避免清单引用包含自身的 Git SHA。公开后还会从 Release 重新下载 ZIP 和校验文件，复算 SHA-256、解包验证五文件安装副本，并核对 Latest API 指向同一 tag。任一步未完成，都不能写成 GitHub Release 已更新。

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
