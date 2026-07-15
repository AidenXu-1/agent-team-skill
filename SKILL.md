---
name: agent-team
description: Build and maintain low-context multi-agent or multi-session teams with durable file-based handoff, factual event logs, independent review, and explicit user gates. Use for software, AI product, content, operations, research, consulting, automation, or other projects that need separate management, execution, and review roles without relying on one long-running conversation.
---

# Agent Team

利用会话之间上下文隔离的机制，一个会话就是一个部门，每个会话职责分明，专心做好属于自己的活，高效协作。并利用项目文件夹本地数据存储的项目留痕方式和巧妙的管理机制，便于更换新的会话使部门能长期稳定工作。

## 默认热路径

1. 判断项目交付物、现有项目文件夹结构和最小必要会话团队。
2. 让用户确认部门配置与会话模式后再创建协作层。
3. 每个新部门会话首次按四文档接班：`上岗引导.md → 岗位说明.md → 交接班文档.md → 收件箱.md`；同一会话只在首次开启时读取上岗引导，后续不重复读取。
4. 手上只做一件；任务用唯一 ID 和原子状态工具流转，收件箱只是自动索引信息，跨会话消息只发“任务 ID + 短状态”。
5. 节点完成后提交产出、已验证/未验证项和错题自检，以 `TASK_STATE_OK` 作为完成收据；只在真实轨迹事件发生时追加事实日志。
6. 会话变重时说明原因并主动询问用户是否换班；只有用户明确同意或主动提出“换会话 / 换班”要求后，才创建全新会话接班。
7. 正式部门在办时，只有用户主动提出独立需求，统筹部才判断是否创建只绑定一个 TASK 的临时外包；未启用时不增加正式部门热路径。

## 必守边界

* 先有能说明目标、交付物和验收标准的项目地基，再创建 `docs/collaboration/`。Agent 必须亲自阅读并复核地基的语义完整性，再用 `--foundation-file` 显式传入已复核文件；脚本只检查路径、编码、类型、大小和非空等机械安全性，不替 Agent 猜测文档是否真正说清业务。已有协作层时先读现状，检查是否存在异常冲突，不直接覆盖，若有异常提醒用户并给出优化方案。

* 团队必须包含管理层、执行层、审核层；最小盘是 `lead,do,review`，不要为“多 Agent”硬拆部门。

* 创建协作层、首次创建部门会话、增删/替换部门、改变跨会话路由或通知模式前，必须获得用户确认。

* 本 Skill 只搭协作机制，不替业务部门直接完成业务代码或产物。

* 产品体验、范围取舍、设计方向、发布/外发、明显成本和隐私安全风险由用户确认；建议下一步不等于授权。

* 派单必须写验收出口和 1–3 个失败/异常路径。涉及用户可见内容时，审核要覆盖实际用户出口，不能只测底层。

* 设计意图预览默认为可选项。只有用户明确提出要看预览、原型、设计稿、视觉方案或“先看效果”，或已确认任务把预览列为交付物时，才制作预览。普通 UI 实现不因流程额外制作预览。被触发的预览必须可直接看到，并尽量与后续实现共用布局、内容、状态和设计 token；无法同源时明写保真度差距。它仍只代表设计意图，最终 UI 以运行中的真实页面、构建或打包态为准；实现若对已确认方向产生实质偏离，必须重新请用户确认。

* 审核部门亲自验证，只回结论和证据，不继承执行部门长上下文，不直接返工、放行或改产物。

* 用户确认正式收口后才检查并提交本节点相关 Git 变更；commit 不代表发布、外发或上线。

* 任务执行状态、业务阶段、用户授权是三条独立轴，禁止用“测试通过”一类业务语义代替任务所有权或用户授权。

* 已有协作层的协议版本不同时，先运行显式升级并保留备份，不直接混入新规则。

## 低上下文规则

* 四文档是新会话的固定入口，不生成第五份接班摘要。交接班文档保存当前摘要，`tasks/` 保存单任务真值，收件箱只显示活动任务索引，日志保存冷事件；四者不复制任务正文。

* 新会话首次读完四文档后，只在收件箱或交接班文档指向当前任务时读取对应任务 JSON。处理任务期间不刷新收件箱；完成、阻断或需要选择下一件时再读。

* 默认不读日志、长报告、决策正文、其他部门正文、代码 diff 或完整测试证据；当前任务明确依赖时再读。

* 根据任务、文档权威性、遗漏风险和当前可用能力自主决定读取范围，不限制文档数量，不规定正文必须全文或局部读取。只检查索引或元数据时，不得声称已经覆盖正文。

* 只有反复出现、确定、需要一致性的机械操作才写入脚本；语义检索、正文理解和证据取舍由 Agent 负责。

* 会话出现反复遗忘边界、与项目文件矛盾、偏离当前任务或质量明显下降时，先告诉用户具体原因和继续使用旧会话的风险，再询问是否换班；未获明确同意不自动换会话。

## 事实日志

日志默认不读，只在事件发生时写入部门 ISO 周日志的身份分区。正式部门和临时外包共用一份周文件，但物理分为“正式部门日志”和“临时外包日志”；临时外包再按 TASK 分组。日志只保存可核验事实，不写“经验、启示、方法论”或完整聊天；项目复盘时再根据事实总结。

必须记录五类改变项目轨迹的事件：

* `MILESTONE`：可交付阶段节点完成。

* `CHANGE`：已确认的需求、范围、优先级、体验或验收标准变化。

* `CORRECTION`：用户明确指出 Agent 误解、遗漏、越权、误导或错误，并发生调整。

* `DECISION`：关键方案被选择、否决或替换。

* `INCIDENT`：值得倒查的失败、阻断、风险或异常及其处理结果。

普通回复、工具调用、重复确认、临时命令失败和不改变边界的措辞调整不记。`CHANGE`、`CORRECTION`、`DECISION` 发生后立即记录；`MILESTONE` 在节点完成时记录；`INCIDENT` 只记影响项目判断或进度的事件。

写日志使用生成的确定性工具，不先读取日志：

```bash
python3 docs/collaboration/scripts/agent_team_log.py append \
  --department "开发部" --task-id TASK-YYYYMMDD-XXXXXX \
  --type CORRECTION --initiator user \
  --fact "Agent 将内测包误写为可真实发码，用户要求区分两者" \
  --trigger "用户明确纠偏" --impact "README 与后续派单口径" \
  --result "已恢复预验证/内测/production 边界" \
  --pointer "docs/spec.md"
```

脚本负责带时区时间、唯一事件 ID、周文件创建、末尾原子追加和短收据；不得输出日志正文。一个事件只登记一次：项目级变化和决策由统筹部记录，部门局部事件由发生部门记录，其他部门只引用事件 ID。可复发的 `CORRECTION` 另外在共享错题集写“错误/正确做法”，并引用事件 ID；不要复制事件全文。

临时外包写日志时还要传 `--executor-type temporary --executor-id ... --parent-department ...`。工具只允许它写入父部门的临时板块。正式吸收后，父部门只在正式板块增加一条引用 TASK、delivery 和正式证据的 MILESTONE，不复制外包原始日志。

## 团队诊断

用户已说明交付物和会话模式时直接诊断，不重复追问。信息不足时一次只问一个关键问题：最终交付什么；会话由工具自动创建，还是用户手动创建窗口。

| 触发                                      | 建议角色                           |
| --------------------------------------- | ------------------------------ |
| App / Web / SaaS / Vibe Coding          | `lead,product,design,dev,test` |
| 资料收集、事实核验重                              | 加 `research`                   |
| 方案、排期、资源配置重                             | 加 `planning`                   |
| 数据、批处理、自动化重                             | 加 `data` / `auto`              |
| 内容或增长持续生产                               | 加 `content` / `growth`         |
| 隐私、权限、密钥、生产配置                           | 按风险节点加 `security`              |
| 成本敏感或有付费项                               | 按成本节点加 `finance`               |
| 无明显专用分工                                 | `lead,do,review`               |

非软件项目不要硬套产品、设计、开发、测试。缺少专用地基时，先问清目标、交付物、对象、验收、资源和风险，再由用户确认是否创建最小业务地基。

AI 产品不单独创建 AI 部门：产品部负责完整产品规划和 AI 行为验收目标；开发部负责包括模型接入、Prompt、RAG、Agent、评测、成本、延迟与降级在内的全部技术实现。只有存在真正独立的长期决策权和交付边界时才拆新部门，不能按技术名词拆部门。

## 创建协作层

用户确认后运行：

```bash
python3 <skill目录>/scripts/scaffold_team.py "<项目目录>" \
  --profile "互联网 AI 产品 + UI + 质量关" \
  --roles "lead,product,design,dev,test" \
  --session-mode manual \
  --foundation-file docs/spec.md
```

没有适用地基且用户确认补最小地基时，再加：

```bash
--allow-without-foundation --create-minimal-foundation \
--foundation-goal "..." --foundation-deliverable "..." \
--foundation-audience "..." --foundation-acceptance "..." \
--foundation-resources "..." --foundation-risks "..."
```

脚本生成协议版本、路由表、四文档、部门表、会话启动状态、稳定路径的任务 JSON、共享报告模板和按需创建的日志目录，以及确定性的 `agent_team_log.py`、`agent_team_task.py`、`agent_team_session.py` 和 `agent_team_temporary.py`。脚本拒绝符号链接越界、并发覆盖、重复角色、缺三层和未确认会话模式；地基内容质量仍由调用脚本前的 Agent 负责。

## 任务事务

* 派单、领取、阻断、等用户、恢复、完成和核收只通过生成的 `agent_team_task.py`。收件箱禁止手工编辑。

* 执行状态只用 `queued / claimed / blocked / waiting_input / completed / acknowledged`；业务阶段写入 `domain_stage`；用户授权记录写入 `authorization_state` 和证据指针。`user_required / user_rejected` 禁止领取。

* 同一部门同时只有一条 `claimed`；`blocked / waiting_input` 不妨碍领取独立任务。恢复旧任务前必须先结束当前 `claimed`。

* 完成时校验本地产物真实存在且位于项目内；外部产物必须显式标记。任务完成必须提供产出、已验证、未验证和错题自检，收到 `TASK_STATE_OK` 后才能唤醒统筹部。

* 审核层任务必须提交本部门 `报告/` 下的审核报告；报告必须带与任务一致的 YAML 元数据，并同时列为本地产物，否则不能完成。

* 统筹部只能核收 `completed` 任务；`acknowledged-by` 必须精确匹配会话状态中当前已登记的 `统筹部/会话ID`，用于防止普通部门误操作，核收后状态为 `acknowledged`。

* `TASK_STATE_OK` 只证明状态已持久化和本地产物路径已校验，不证明业务质量。脚本参数中的领取人、核收人和授权证据只作审计记录，不冒充身份认证。

## 单 TASK 临时外包

* 内部统一模型为 `temporary_executor`，必须绑定一个 TASK 和一个 `parent_department`。用户侧按父部门称“临时开发外包”“临时设计外包”等；数据模型和 preflight 不写死开发部。当前首轮完整运行适配只开放临时开发外包，其他父部门尚未完成专业成果版本与吸收适配时只能讨论和预检，不能声称已可执行。

* 只有用户主动发起后，统筹部才能判断和创建。统筹部不能主动扩容、拆需求、自动重派或让临时执行者从共享任务池自行领取。

* 正式在办、阻断、等待输入和尚未吸收完成的临时任务都用 `write_paths / shared_contracts / external_effects / base_revision / owner_task` 声明影响。声明不足只报 `manual`，写路径、共享契约或 ignored 生成物无法证明独立时必须阻断或转人工；工具不能自动 stash、reset 或 checkpoint 正式部门工作。

* 临时 workspace 位于项目内 `.agent-team/workspaces/TASK-ID/`，项目根必须忽略 `/.agent-team/`。Git ignore 不能证明 watcher、构建器或同步工具也会忽略该目录；创建前由 Agent 检查这些项目配置并把证据写入创建请求。worktree 只提供协作隔离，不冒充 OS 沙箱；首轮禁止密钥、生产、付费和真实外部副作用。

* workspace 内只生成一份 `.agent-team/临时执行规则.md`，作为临时会话的身份、权限、日志和收口入口。它按 TASK 指针读 Spec、相关 ADR、conventions、代码和测试，默认不读父部门完整岗位说明、收件箱、交接、progress 和长期报告。专业标准可以继承，组织身份和正式权限必须重写。

* 需求实质变化使用带 expected brief revision 的 `amend`，同一事务重新判断并行条件、增加 attempt、清空旧候选 / delivery / integration，并重生成临时规则。临时会话重新确认新 digest 前不能固定候选或 submit。已 submit 或被统筹接管时先走显式 `rework`，不能直接 amend。

* 先固定可复查候选，再把用户确认、明确委托或不适用记录绑定到该候选 revision 和 tree digest。workspace 产生新 commit 后必须固定新候选并重新确认，工具不能把旧确认自动套到新版本。独立子 Agent 审查只在用户要求时调用，只给结论和证据；修复形成新候选。delivery submit 后使用受保护 ref 留证，临时会话进入 standby，不能自称已集成。

* 正式体系只验证一次“准备成为正式结果的完整候选”。delivery 与未来正式 tree 相同时直接验证 delivery；main 前进、存在冲突或有集成修改时才按需形成候选集成态。正式通过必须绑定已完成并由统筹核收的审核层 TASK、本地正式报告、tested commit/tree 和未覆盖项，不能只填一段“测试通过”文字。进入 main 的 tree 必须等于已测试 tree，main 漂移或测试后修改都要求重测。

* 清理前同时通过成果吸收和知识吸收，并验证 delivery 保护 ref 仍指向正确 commit/tree。知识默认只回到所属正式部门与项目全局 Spec、ADR、conventions、progress 或错题集；其他部门只有明确受影响时才介入。未集成或用户未明确放弃时不得清理，长期无回复只进入 standby。创建、晋升和清理的半失败必须先运行对应 reconcile，不能根据名称猜测重做或删除。

* `cleanup` 只清理受控 workspace / branch；存在真实临时会话时返回 `ARCHIVE_THREAD_REQUIRED:<thread_id>`，不能把 TASK 内部状态冒充成真实会话已经归档。统筹部先检查当前宿主能力：有自动归档工具时立即调用，成功后运行 `session-mark --state archived --archive-mode automatic --evidence "host=<真实工具> thread_id=<真实ID> archived=true"` 并继续原流程；没有自动归档工具时运行 `archive-request --task-id ...`，把工具生成的具体会话名称、ID 和固定回复口令原样提醒用户，然后停在该确认门。只有用户回复“我已将该会话归档”，才运行 `session-mark --state archived --archive-mode manual --user-confirmation "我已将该会话归档" --evidence "当前用户确认消息"` 并继续原流程。用户未回复、回复含糊、归档失败或回执写入失败时保留 `temporary_session=standby`，不得静默越过；自动工具对 inactive / 可能已归档会话返回不确定失败时，先读取真实状态，仍无法确认时可对同一 ID 受控执行 `unarchive → archive`，以最后一次明确成功的 archive 收据为准。若创建阶段就已放弃、从未产生真实 thread ID，则返回 `NO_THREAD_ARCHIVE_REQUIRED` 并把会话状态收口为 `cancelled`。旧协议升级时，缺少可信宿主收据的 `archived` 会话必须退回 `standby` 并返回同样的归档动作，不能继承旧的账面结论。

* 所有临时生命周期机械操作使用 `agent_team_temporary.py`。普通任务继续走原有三个工具；临时功能未启用时，正式部门不增加必读文件或状态步骤。

## 会话模式与换班

* `manual`：只生成文件和上岗清单；不得声称已经创建部门会话。

* `auto`：生成文件后，用当前环境的会话工具创建各部门新会话、发送上岗引导，再把真实会话 ID 和外部证据写入会话状态；会话工具刷新部门表派生索引。任一步失败都如实回退为人工。

* 自动模式的每次外部调用后立即用 `agent_team_session.py` 记录 `created / onboarded / registered / failed`；重试先读状态，不重复创建已成功会话。

* 会话变重只触发“换班建议”：说明已观察到的问题、换班的好处和当前在办事项，询问用户是否执行。用户未回复或未明确同意时，保留当前会话。

* 用户说“换会话 / 切换会话 / 换班”即授权同部门换班：旧会话先更新交接与必要事实日志，再创建同项目新会话，发送四文档路径。新会话确认接班并登记新 ID 后，最后归档旧会话。

* 不使用会复制旧历史的 fork。创建、发送、接班、登记或归档失败时保留旧会话。

## 用户常用口令

* `接班`：当前会话读取本部门入口、恢复当前状态；存在明确可继续的任务时可以接着做。

* `先接班，不要开始任务`：只恢复职责、状态和待办，汇报后停下。

* `交班`：更新交接班文档和必要事实日志，仍使用当前会话。

* `换班 / 换会话`：授权同部门创建全新会话接班；新会话登记成功后才归档旧会话。

## 维护与验证

* 增加部门：`scaffold_team.py <项目> --add-roles "<ids>"`；脚本同一事务更新部门表、路由表、会话启动状态和部门目录。

* 旧协作层升级：`scaffold_team.py <项目> --upgrade-collaboration`。如旧收件箱存在待办/回报正文，脚本拒绝猜测迁移，要求先处理清楚；升级成功前保留带时间戳备份。

* 减少或合并部门：先把在办事项和历史指针交代清楚，再调整部门表与岗位边界，不删除历史。

* 通知能力只在上岗/接班时登记一次；后续按部门表的自动/人工模式执行。

* 修改本 Skill 后运行项目验证器、`quick_validate.py` 和 Python 编译检查；再同步并验证全局安装目录只包含 `SKILL.md`、`agents/openai.yaml`、`scripts/scaffold_team.py` 和 `scripts/temporary_executor_runtime.py`。
