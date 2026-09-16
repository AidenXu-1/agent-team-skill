# agent-team

把多 Agent 协作的真值写进项目文件：团队搭建、角色分工、会话路由、任务交接与协议修复。

它不是替你写业务的 Agent，而是一套**给 Agent 用的协作机制** —— 用项目文件保存分工、任务状态、审核结论和接班上下文，让跨会话、跨部门的 AI 团队不靠记忆运转。

- 仓库：<https://github.com/AidenXu-1/agent-team-skill>
- 当前版本：`v2.1.1`（tag `v2.1.1`）
- 运行时协议版本：`1.5.1`

## 适用什么项目

按交付物类型诊断最小必要团队，不为"多 Agent"硬拆部门：

| 触发 | 建议角色 |
| --- | --- |
| App / Web / SaaS / Vibe Coding | `lead,dev,test` |
| 资料收集、事实核验重 | 加 `research` |
| 方案、排期、资源配置重 | 加 `planning` |
| 数据、批处理、自动化重 | 加 `data` / `auto` |
| 内容或增长持续生产 | 加 `content` / `growth` |
| 隐私、权限、密钥、生产配置 | 按风险节点加 `security` |
| 成本敏感或有付费项 | 按成本节点加 `finance` |
| 无明显专用分工 | `lead,do,review` |

团队必须包含管理层、执行层、审核层，最小盘是 `lead,do,review`。非软件项目不硬套产品、设计、开发、测试。

## 快速开始

依赖 Python 3，两个脚本只用标准库，无第三方运行时依赖。整个目录作为一个 Skill 放到宿主的 skills 目录即可加载。

用户确认团队方案后，生成协作层：

```bash
python3 <skill目录>/scripts/scaffold_team.py "<项目目录>" \
  --profile "互联网 AI 产品 + UI + 质量关" \
  --roles "lead,dev,test" \
  --session-mode manual \
  --foundation-file docs/spec.md
```

没有适用地基且用户确认补最小地基时，追加：

```bash
--allow-without-foundation --create-minimal-foundation \
--foundation-goal "..." --foundation-deliverable "..." \
--foundation-audience "..." --foundation-acceptance "..." \
--foundation-resources "..." --foundation-risks "..."
```

脚本会在项目下生成协作文件与运行工具：`agent_team_task.py`（原子任务事务）、`agent_team_session.py`（会话登记与换班）、`agent_team_log.py`（事实日志追加）等。当前调度固定为 `manual-degraded`，`auto` 仅支持已授权的会话创建和短通知：不轮询、不承诺无人值守或 Token 下降。

## 工作方式

核心思路一句话：**会话只留当前工作集，项目文件存长期真值。**

| 机制 | 做什么 |
| --- | --- |
| **三层团队** | 管理层 / 执行层 / 审核层，角色定义真值在 `scaffold_team.py` 的 `ROLE_DEFS` |
| **四文档接班** | 固定四个入口文件，一次 `onboard-bundle` 输出岗位、收件箱、交接机器区块、绑定当前 TASK/候选的恢复说明和当前 TASK；不生成第五份摘要，冷历史留在 `tasks/` 和日志里 |
| **任务事务** | 同一时刻只有一个活动切片：一个执行 owner、最多两个审核 gate、一个当前候选。派单 / 领取 / 阻断 / 候选绑定 / gate verdict / 完成 / 核收只通过 `agent_team_task.py`；状态只有 `queued / claimed / blocked / waiting_input / completed / acknowledged` |
| **事实日志** | 默认不读，只写。只记 `MILESTONE` / `CHANGE` / `CORRECTION` / `DECISION` / `INCIDENT` 五类改变项目轨迹的事件，带事件 ID 与原子追加；普通回复、工具调用和重复确认不记 |

### 会话模式

- `manual`：用户建窗口并发送上岗引导后，也必须用同一会话 ID 依次登记 `created / onboarded / registered`；统筹部 registered 后才能派单
- `auto`：生成文件后由当前环境的会话工具创建各部门新会话、发送上岗引导，再把真实会话 ID 写入会话状态；任一步失败都如实回退为人工

### 常用口令

`接班` 恢复职责与当前任务；`先接班，不要开始任务` 汇报后停下；`交班` 更新交接和必要日志；`换班 / 换会话` 授权创建同部门新会话，新会话登记成功后才归档旧会话。

## 必守边界

- 项目地基先说明目标、交付物和验收标准，再创建 `docs/collaboration/`；已有协作层异常时不覆盖并给方案
- 创建协作层、首次创建部门会话、增删/替换部门、改变跨会话路由或通知模式前，必须获得用户确认
- 本 Skill 只搭协作机制，不替业务部门直接完成业务代码或产物
- 审核部门亲自验证，只回结论和证据，不继承执行部门长上下文，不直接返工、放行或改产物
- 用户确认正式收口后才检查并提交本节点相关 Git 变更；commit 不代表发布、外发或上线
- 临时外包是低频旁路：协议 1.5 对新的临时外包请求统一返回 `TEMPORARY_EXECUTOR_P2_REQUIRED`，不绕过单 owner 闸门

## 目录

| 路径 | 说明 |
| --- | --- |
| `SKILL.md` | 规则正文：默认热路径、必守边界、低上下文规则、事实日志、团队诊断、创建协作层、任务事务、用户闸门与汇报、会话模式与换班、维护与验证 |
| `scripts/scaffold_team.py` | 生成协作层的唯一真值脚本，内含 `ROLE_DEFS`；提供事实日志追加器、原子任务队列、候选绑定与 gate 校验的实现 |
| `scripts/temporary_executor_runtime.py` | 单 TASK 临时外包生命周期工具（declare / preflight / provision / reconcile / session / amend…），仅用于维护升级前已存在的 1.4 legacy TASK |
| `references/temporary-executor.md` | 仅在用户主动提出临时外包时按需加载；解释启用边界、授权规则与遗留资源识别方式 |
| `agents/openai.yaml` | Agent 接口声明：`display_name`、`short_description`、`default_prompt` |

## 维护与验证

- 增加部门用 `--add-roles "<ids>"`；停用部门先取得真实归档回执再 `agent_team_session.py retire`，再 `--deactivate-roles ...`
- 部门表丢失或损坏时运行 `agent_team_session.py rebuild-registry`，只从会话状态真值重建
- 跨协议升级先由统筹冻结，再用 `--upgrade-collaboration`；1.4 TASK 保持原字节并作为冷历史
- 修改本 Skill 后运行项目验证器、`quick_validate.py`、Python 编译和 diff 检查
- 同步全局安装、发布或逐项目升级都是独立授权动作；源码候选 PASS 不能冒充已安装、已发布或可用
