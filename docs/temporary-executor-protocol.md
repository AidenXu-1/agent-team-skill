---
title: Agent Team 单 TASK 临时外包协议
status: implemented
version: 0.3.0
product_version: 2.0.1
runtime_protocol_version: 1.4.6
date: 2026-07-16
---

# Agent Team 单 TASK 临时外包协议

## 0. 版本关系与实现状态

本文描述的临时外包链路已经进入实现态。三个版本号承担不同职责，不能互相替代：

- 产品与发布版本 `2.0.1`：用户安装、Git 标签和 Release 使用的稳定产品标识。本轮安全加固不改变公开产品线。
- 运行协议 `1.4.6`：写入项目 `docs/collaboration/协议版本.json` 的内部数据与工具契约。它从 `1.4.5` 升级，用于严格 JSON、影响范围双向冲突、会话 ID 唯一性、受管文件内容校验和待归档查询等机械真相。
- 文档修订号 `0.3.0`：只表示本文的表述与验收清单修订，不代表新的产品发布，也不能代替项目内运行协议号。

因此，一个安装包可以继续叫 `2.0.1`，由它生成或升级的协作层使用运行协议 `1.4.6`，而本文单独以 `0.3.0` 记录协议说明的修订进度。

## 1. 产品定义

当正式部门正在处理任务 A，用户又提出相对独立的新需求 B 时，用户可以要求统筹部判断 B 是否适合交给临时外包并行处理。

通过后，系统创建一个只绑定 B 的 `temporary_executor`。用户直接在临时会话中沟通、调整和验收；正式体系在交付后负责审核、吸收和正式晋升。临时执行者随 TASK 生命周期存在，不成为永久部门或永久席位。

用户侧名称根据父部门显示，例如“临时开发外包”“临时设计外包”“临时研究外包”。通用模型和 preflight 不写死开发部；当前首轮完整运行适配只开放临时开发外包，其他父部门在专业候选、验收和吸收适配完成前不能进入执行链。

## 2. 用户旅程

1. 用户主动提出需求 B，并询问或授权统筹部创建临时外包。
2. 统筹部检查 B 与正式任务、其他临时任务和项目阶段是否可以安全分开。
3. 通过后创建唯一 TASK、隔离 workspace、临时执行规则和临时会话。
4. 用户在临时会话中直接多轮沟通；正常过程不例行汇报统筹部。
5. 任务内部、可逆的小调整可以继续，但 TASK 的当前 brief 必须同步更新。
6. 范围、共享契约、权限或风险边界发生实质变化时，临时执行者暂停并升级统筹部。
7. 用户认为方向满意后固定一个可复查候选，再把确认、明确委托或不适用记录绑定到该候选 revision 和 digest。
8. 用户可以要求调用不继承长对话偏见的独立子 Agent 审查候选；审查者只给结论和证据，不直接返工。
9. 修复后形成新候选，旧候选上的确认、审查和测试证据按影响范围失效。
10. 最终成果成为 delivery，临时执行者 submit 后进入 standby。
11. 统筹部接管，完成正式验证、成果吸收和知识吸收。
12. 只有成果已经正式吸收或用户明确放弃，并且证据与资源归属核对完成后，才能清理 workspace、普通临时分支和会话。

## 3. 用户触发与统筹权限

- 每次创建都必须由用户主动发起。
- 统筹部不能因为工作量增加而主动扩容、拆分需求或创建外包。
- 用户只询问“能不能并行”时，判断通过后仍需确认创建。
- 用户已明确表达“如果可以就帮我开”时，判断通过后可以直接创建。
- 统筹部不能自动 stash、reset、提交或 checkpoint 正式部门的未提交工作。

## 4. 通用模型与父部门

每个临时执行者必须包含：

- `executor_type=temporary`
- 唯一 `executor_id`
- 唯一 `task_id`
- `parent_department`
- 与父部门对应的用户侧显示名

父部门负责提供专业质量标准和最终知识归属。临时规则只能继承真正同义的专业要求，不能继承父部门的组织身份、长期职责或正式写权限。

不同父部门可以有不同交付形态：

- 开发类：commit、tree 和测试证据。
- 设计类：稳定设计版本、预览和产物摘要。
- 产品类：稳定方案版本、需求文档或原型。
- 研究类：稳定报告、来源清单和证据快照。
- 测试类：测试记录、缺陷证据和复现材料。

底层生命周期保持一致；专业验收和正式吸收方式由父部门决定。

## 5. 不做什么

- 不创建永久第二部门或永久第二席位。
- 不创建临时部门目录、岗位说明、正式收件箱、交接班文档和长期报告目录。
- 不让临时执行者成为共享任务池的主动领取者。
- 不做自动负载均衡、自动扩容、自动拆任务、心跳、自动超时、自动重派。
- 不要求正常过程向统筹部例行汇报。
- 首轮不允许涉密、生产、真实发送、发布、购买、付费或其他真实外部副作用。
- 不把 Git worktree 描述为 OS 权限沙箱。
- 首条工程切片用临时开发外包验证，但字段、日志和生命周期不能写死为开发部。

## 6. TASK、临时规则与日志的职责

### TASK

TASK 保存轻量控制真值和长期证据索引：

- 当前 brief、brief revision、验收出口和失败路径。
- 用户授权和确认指针。
- executor、父部门、workspace、base revision 和允许范围。
- 共享契约、外部影响和并行判断结果。
- 执行、用户验收、晋升和临时会话状态。
- attempt、候选版本、delivery、正式验证和最终吸收指针。
- 临时规则版本、digest、来源版本和确认时间。

TASK 不保存完整聊天、临时规则全文、长篇审查推理、测试控制台全文、重复日志正文或已经进入正式文档的知识副本。

### 临时执行规则

每个 workspace 生成一份唯一入口：

`.agent-team/workspaces/TASK-ID/.agent-team/临时执行规则.md`

规则正文只服务当前 TASK，至少说明：

- 临时身份、父部门、当前目标和 workspace。
- 允许写入范围和禁止路径。
- 继承的专业质量规则与重新声明的临时权限。
- 必读项目事实与默认不读的正式部门资料。
- 必须暂停升级的情形。
- 日志、候选、审查、delivery、submit、standby 和清理规则。

TASK 只保存规则摘要证据；规则正文随 workspace 清理。

### 日志

临时执行者与父部门共用同一份 ISO 周日志，但文档内固定为两个物理板块：

1. `## 正式部门日志`
2. `## 临时外包日志`

临时外包板块再按 TASK 分组。事件必须带 `task_id`、`executor_type`、`executor_id` 和 `parent_department`。

日志仍只记录 `MILESTONE / CHANGE / CORRECTION / DECISION / INCIDENT`。正式与临时时间线不能交叉混排，事件保持追加事实语义。完成吸收后，父部门只在正式板块增加一条 MILESTONE，引用 TASK、delivery、正式吸收和审核证据；不复制外包原始日志。

## 7. 阅读边界

临时会话从 workspace 内的临时执行规则读取身份、权限、日志和收口流程。

它可以按 TASK 指针读取：

- 项目唯一准绳，例如 `docs/spec.md`。
- 相关 ADR。
- `docs/conventions.md`。
- 相关代码、自动化测试和必要证据。

默认不读父部门完整岗位说明、收件箱、交接班文档、项目 progress 和长期报告。专业标准由规则生成器选择、提炼或引用；父部门权限必须改写为临时权限。

## 8. Workspace 与主控制根

- 临时 workspace 固定在项目内 `.agent-team/workspaces/TASK-ID/`。
- `scratch/` 继续只放实验、探针和临时输出，不放 worktree。
- `docs/collaboration/` 的权威副本只位于主 worktree。
- linked worktree 复制出的协作层没有权威性。
- 控制工具从任何 worktree 运行时，都必须发现并写入主控制根。
- 临时分支修改 `docs/collaboration/` 时，submit 和正式晋升必须阻断。
- 创建前必须检查 ignore、watcher、构建扫描、同步工具、符号链接、submodule、tracked、untracked、ignored 和清理边界。Git ignore 只证明 Git 不跟踪，Agent 必须另行记录 watcher、构建和同步扫描边界证据。

worktree 只提供协作隔离和晋升检测。首轮禁止临时执行者接触密钥、隐私数据、生产环境和真实外部副作用。

## 9. 并行判断

正式任务和临时任务逐步提供机器可读影响声明：

- `write_paths`
- `shared_contracts`
- `external_effects`
- `base_revision`
- `owner_task`

判断结果至少区分：

- `safe`：现有证据足以确认独立。
- `unsafe`：存在明确冲突。
- `manual`：声明不足，需要人工判断。
- `waiting_base`：依赖正式任务尚未形成的安全基准。

主工作区存在未提交变更时：

- B 与脏内容、写路径、共享契约、生成物和外部影响明确独立，可以从已提交 base 开始隔离工作，但不能提前进入 main。
- B 依赖 A 的未提交内容，或影响范围重叠，默认等待 A 形成安全 checkpoint。
- 用户要求 B 优先时，由用户决定是否暂停 A 并 checkpoint；工具不自动处理 A 的脏工作。
- 缺少 A 的影响声明时，只能报告 `manual/unknown`，不能声称已安全验证。

## 10. Brief amend 与升级条件

任务内部、可逆且不扩大授权边界的调整可以继续。每次实质 amend 必须携带 expected revision，在同一事务中：

1. 验证当前 revision。
2. 更新 current brief。
3. 重新检查影响声明和并行条件。
4. 成功后生成新 revision；失败时不留下半更新状态。

以下变化必须暂停并升级统筹部：

- 修改 Spec、需求边界或验收合同。
- 修改共享 API、Schema 或数据契约。
- 扩大写入范围。
- 引入新依赖。
- 数据库、迁移、认证、权限、密钥或隐私。
- 付费、生产、发布、购买或真实外部发送。
- 与正式任务假设发生冲突。
- 当前需求已经成为实质不同的新任务。

## 11. 多轴状态与证据失效

状态保持多轴：

- `execution_state`：`queued / claimed / blocked / waiting_input / completed / acknowledged`
- `user_acceptance`：`pending / confirmed / rejected / delegated / not_applicable`
- `promotion_state`：`not_submitted / submitted / reviewing / waiting_base / ready / integrated / archived / cancelled / abandoned`
- `temporary_session`：`provisioning / active / standby / archived / failed / cancelled`

关键语义：

- 用户满意只改变用户验收状态并允许固定候选。
- `completed` 表示临时执行者已经冻结并提交 delivery。
- `acknowledged` 表示统筹部已经接管。
- `integrated` 表示成果已经进入正式权威载体。
- `promotion_state=archived` 表示成果、知识、证据和临时 Git 资源已经完成收口。
- `temporary_session=archived` 只表示当前归档工具已经返回成功，或用户明确告诉 AI 已完成手动归档；临时 Git 资源已清理但真实会话尚未取得其中一种回执时，仍保持 `standby`。

每次返工或实质 amend 增加明确 attempt，并生成新的 `candidate_revision`。任何修改都必须清空旧 candidate、review、delivery 和 integration，退回 `not_submitted`，重生成临时规则并要求会话重新确认。已完成的前置清点和吸收收据同时从当前证据中失效，只保留带旧 attempt 的历史快照供倒查。工具不能自动把旧用户确认、审查、测试或吸收证据套到新成果上。

`blocked / waiting_input` 只是暂停状态，不是对旧准入结论的保鲜。临时 `resume / rework` 和正式 `resume` 都必须在共享任务锁内重做当前写路径、共享契约和外部影响检查。冲突仍在时保持阻断，`rework` 不增加 attempt，两条路径都不能只靠状态转换回到 claimed。

临时会话一旦登记真实 thread ID，后续 failed 重试、规则重建、amend 或 rework 后重新 active 都必须继续使用原始字符串完全相同的 ID，不能用新 ID 覆盖未归档会话。ID 必须不含空白、不以 `=` 开头且不超过 300 字符，保证它能无歧义地放进归档回执。workspace 创建事务 verified 后，temporary session 仍处于 provisioning、真实会话已经创建但后续上岗失败时，failed 状态必须登记该 ID，最终清理不能误判为从未创建会话；创建事务尚未 verified 时必须先 reconcile，不能提前登记会话。外部 `session-mark --state cancelled` 一律拒绝；`cancelled` 只由无真实 thread ID 的 abandoned cleanup 在资源已验证清理后内部写入。

## 12. 候选成果与可选独立审查

- 用户可见成果默认需要用户确认；纯内部任务可以由用户明确选择 `delegated`，确实不适用时记录 `not_applicable` 和理由。
- 先固定稳定候选：代码用 commit/tree，其他产物用稳定版本和 digest；随后把验收证据绑定到该 revision 和 digest。任何新候选都把验收退回 pending。
- 独立子 Agent 只在用户要求时调用。
- 审查输入限定为 TASK、验收标准、相关项目事实、候选版本、变化范围和验证入口。
- 审查者只提交结论和证据，不直接修改成果。
- 修复由临时执行者完成，并形成新候选。

`submit` 是候选生成阶段的单向边界。提交后不再允许 `candidate` 或 `review` 改写交付证据；需要修改时必须显式 `rework`。正式集成记录只能在统筹已接管且尚未 integrated 时写入。`integrated`、资源已清理和会话已归档都是不可回退的机械真值；重试 reconcile 只返回幂等收据，不重开旧状态。

## 13. Delivery、候选集成态与正式验证

delivery 是临时执行者的正式交付版本。代码类 delivery 必须使用受保护 ref 或 bundle 保存，避免普通分支和 workspace 清理后被 Git GC 回收。

正式体系需要验证“准备进入 main 的最终 tree”，但不建立重型、常驻候选区，也不重复安排两轮正式测试。

### 按需策略

- delivery tree 与未来 main tree 完全相同，main 未漂移且没有集成修改：不创建额外候选 workspace，正式部门直接验证 delivery。
- main 已前进但可以自动组合：先计算候选 tree；需要真实构建或测试时才临时检出。
- 存在冲突、胶水代码或集成修改：创建临时候选集成态，并验证修改后的完整结果。
- 非代码产物不套 Git tree 流程，使用父部门的稳定候选、正式验收和权威吸收规则。

正式测试只发生一次：在即将成为正式结果的完整候选上执行。外包自测与可选独立审查不能替代这次正式验证，也不应再触发一次重复的外包分支完整测试。

代码晋升记录 `tested_base`、`tested_commit`、tree OID、测试定义、环境证据和未覆盖项。正式测试证据必须绑定已完成并由统筹核收的审核层 TASK，以及该部门报告目录中的真实报告；报告必须写明 tested commit/tree 和结论。测试后候选发生修改，或 main 已前进，原测试证据失效。只有准备晋升的 tree 与已测试 tree 完全一致，并且 main 仍等于预期 base，才能原子晋升。

## 14. 成果与知识吸收关

清理前必须同时完成：

1. 成果吸收：代码、设计、报告或其他交付进入正式权威载体。
2. 知识吸收：长期有效的信息进入正确的正式位置，或明确记录无需吸收。

知识默认只检查两个主要去向：

### 所属正式部门

- 可复用专业规则。
- 正式部门需要保留的结论和证据。
- 容易复发的错误。
- 正式报告需要引用的内容。
- 正式日志中的一次吸收 MILESTONE。

### 项目全局

- 范围或定义变化进入 Spec。
- 长期架构决定进入 ADR。
- 通用执行规则进入 conventions。
- 项目阶段变化进入 progress。
- 可复发错误进入错题集。

其他部门只有在存在明确跨部门影响时才接收吸收任务，不默认广播，也不逐部门例行更新。

统筹部在 submit 后做轻量前置清点，识别测试前必须确认的定义、约束和验收依据；正式验证后再做最终清点。清点重点读取 TASK、delivery diff 或稳定产物、对应临时日志、交付说明、独立审查、正式验证证据和 workspace 中未纳入交付的文件清单，不逐个阅读依赖、构建缓存和生成噪声。

临时执行者可以提出待吸收候选，但不能自行把判断升级为正式 Spec、ADR、conventions 或正式部门报告。所属正式部门或项目权威负责人确认后完成更新并返回收据。

## 15. 失败恢复与清理

资源创建、晋升和清理必须记录：

- `operation_id`
- 幂等 client key
- `planned / started / succeeded / verified`
- 每个资源的真实 identity
- ownership marker
- 失败阶段和 reconcile 结果

重试前先核对现有资源，不能因客户端超时重复创建会话，也不能根据名称猜测并删除已有 branch、workspace 或文件。幂等 key 必须绑定规范化请求 digest；同 key 不同请求直接冲突。Git 已改变但 TASK 尚未更新时，必须通过 promotion/cleanup reconcile 根据 expected ref、ownership 和实际资源状态恢复。provision operation 未 verified 时，resume、rework、abandon 等常规生命周期全部冻结；promotion operation 未经 reconcile 收口时，重新测试、晋升、返工、放弃和 preflight 吸收同样冻结；cleanup operation 一旦开始，知识吸收等其他写操作也冻结，只能先 reconcile 或完成受控重试。

用户取消只停止继续投入，不自动等于放弃。长期无回复进入 standby，换班继续绑定同一 TASK 和 workspace。

只有满足以下任一条件才进入清理：

- 成果已经 integrated，所属部门和项目全局知识清点完成。
- 用户明确 abandoned，且必要证据已经保留。

清理前核对 delivery 保护、正式吸收证据、未提交文件和资源 ownership。`cleanup` 先安全移除 workspace、处理普通临时分支、删除临时规则；存在真实临时会话时返回 `ARCHIVE_THREAD_REQUIRED:<thread_id>`。此时只允许把 `promotion_state` 写为 `archived`，`temporary_session` 继续保持 `standby`。若创建阶段就已放弃、从未产生真实 thread ID，则返回 `NO_THREAD_ARCHIVE_REQUIRED` 并将会话状态收口为 `cancelled`。

统筹部随后直接根据当前可用工具走两条轻量路径：

1. 当前工具列表里有真实会话归档能力：立即用 TASK 登记的 thread ID 调用；成功后运行 `session-mark --state archived --evidence "host=<真实工具> thread_id=<真实ID> archived=true"`，随后继续。自动调用失败时使用同一句人工提醒。
2. 当前没有可用归档工具：直接提醒用户归档具体会话，并请用户完成后告诉 AI 一声。提醒后保留 `standby`，但不建立脚本硬闸；其他不依赖归档结果的安全工作可以继续。用户之后以任何清楚的说法表示已经完成，运行 `session-mark --state archived --evidence "user_confirmation=<用户确认指针> thread_id=<真实ID> archived=true"` 登记即可。

人工提醒统一使用这一句话：

```text
我目前无法自动归档这个会话。请你手动归档临时外包会话「<会话名称>」（会话 ID：<thread_id>），归档完成后告诉我一声。
```

用户没有回复时，AI 只保留“待归档”的事实，不需要阻断整个项目。两条路径仍把“临时资源已经收走”和“侧边栏会话已经归档”分成两张独立收据。

从旧协议升级时，升级事务必须先备份 TASK。只要旧记录能够通过当前严格解析，按原始字符串精确绑定真实 thread ID、独立的 `archived=true` 与 host 或 user confirmation 来源，就继续保留，大小写不同的 ID 必须拒绝，也不因版本号较早而抹掉真实证据；缺失、冲突或无法验证的 `temporary_session=archived` 才退回 `standby`，并返回 `ARCHIVE_THREAD_REQUIRED:<thread_id>`。资源已清理、会话仍 standby 但 thread ID 为空时，它既不是可提醒的会话，也不是“从未创建”的 cancelled，必须在升级和 `pending-archives` 阶段显式拒绝。`1.4.5 → 1.4.6` 升级保留会话的 created、onboarded、registered、previous thread、证据和进行中的换班 operation 真相。已经完成资源清理但仍在 `standby` 的任务，无论是否刚发生跨版本升级，重复运行升级或只读 `pending-archives` 都会再次返回归档动作，避免遗留会话继续沉默。升级故障的回滚清单同时记录旧任务状态子目录的存在性与权限；回滚必须恢复空目录和原 mode，才能声称逐项校验成功。

## 16. 当前实现边界

当前版本已用单个临时开发外包实现并验证完整通路，通用模型包含 `temporary_executor + parent_department`。

必须覆盖：

- 用户主动触发与统筹判断。
- TASK 绑定唯一 workspace 和临时规则。
- 主控制根发现与协作层副本阻断。
- brief expected-revision amend。
- 候选固定、可选独立审查、delivery submit 和 standby。
- 按需候选集成态、一次合并后正式验证和 tested-tree 晋升。
- 父部门日志双板块。
- 所属正式部门与项目全局知识吸收。
- 创建半失败、返工、长期无回复、换班、取消、放弃和安全清理。
- 容量为 1 且未启用临时外包时，正式部门原有热路径和阅读成本不增加。

当前不做多外包自动调度、所有部门的专业适配、真实外部副作用和 OS 沙箱。
