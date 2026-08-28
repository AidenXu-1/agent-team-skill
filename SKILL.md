---
name: agent-team
description: Build low-context multi-agent teams with durable handoff, independent review, and user gates. Use when a project needs separate management, execution, and review roles.
metadata:
  version: 2.1.1
---

# Agent Team

用项目文件保存分工、审核和接班真值。

## 默认热路径

1. 判断交付物、项目结构和最小团队。
2. 用户确认部门与会话模式后再创建协作层。
3. 新部门会话首次读 `上岗引导.md` 后只运行一次 `agent_team_task.py onboard-bundle --department ...`；它校验 freshness，输出岗位、收件箱、当前 TASK 和交接班机器区块，不带区块外的过期人工“当前任务”，不生成摘要、不读冷历史，后续不重复。
4. 全项目同一时刻只有一个活动切片：一个执行 owner、最多两个审核 gate、一个当前候选。任务用唯一 ID 和原子状态工具流转，跨会话消息只发“任务 ID + 短状态”。
5. 节点完成后提交产出、已验证/未验证和错题自检，以 `TASK_STATE_OK` 为完成收据；只记真实轨迹事件。
6. 依后文评估工作集；只建议换班，用户明确同意后才创建新会话。
7. 临时外包是低频旁路。协议 1.5 暂不允许它另开第二 owner TASK；用户主动提出时读取 `references/temporary-executor.md`，说明当前为 P2 宿主适配缺口，不绕过单 owner 闸门。

## 必守边界

* 项目地基先说明目标、交付物和验收标准，再创建 `docs/collaboration/`。Agent 复核语义并用 `--foundation-file` 传入；脚本只检查路径、编码、类型、大小和非空。已有协作层异常时不覆盖并给方案。

* 团队必须包含管理层、执行层、审核层；最小盘是 `lead,do,review`，不要为“多 Agent”硬拆部门。

* 创建协作层、首次创建部门会话、增删/替换部门、改变跨会话路由或通知模式前，必须获得用户确认。

* 本 Skill 只搭协作机制，不替业务部门直接完成业务代码或产物。

* 产品体验、范围取舍、设计方向、发布/外发、明显成本和隐私安全风险由用户确认；建议下一步不等于授权。

* 低影响测试自动执行。明显妨碍设备使用（如跨 Space 全屏、抢键鼠或重启）时，先说明影响、时长、能否继续工作和退出方式；独占或难退出还须当次确认，阶段授权不能代替。

* 派单必须写验收出口和 1–3 个失败/异常路径。涉及用户可见内容时，审核要覆盖实际用户出口，不能只测底层。

* 设计预览仅在用户明确要求或任务列为交付物时制作；普通 UI 实现不额外制作。预览须可直接查看并尽量与实现同源，无法同源时说明差距；最终以真实运行、构建或打包态为准，实质偏离已确认方向时重新确认。

* 审核部门亲自验证，只回结论和证据，不继承执行部门长上下文，不直接返工、放行或改产物。

* 用户确认正式收口后才检查并提交本节点相关 Git 变更；commit 不代表发布、外发或上线。

* 任务执行状态、业务阶段、用户授权是三条独立轴，禁止用“测试通过”一类业务语义代替任务所有权或用户授权。

* 已有协作层的协议版本不同时，先运行显式升级并保留备份，不直接混入新规则。

## 低上下文规则

* 四文档是固定入口，不生成第五份摘要。`onboard-bundle` 一次输出岗位、收件箱、交接班机器区块和当前 TASK；区块外人工文字不进入默认热入口，也不能充当 TASK 身份。失败时停止接班，由已登记统筹 actor 运行 `rebuild-index`；`tasks/`/日志保存冷历史。

* 新会话通过 freshness 后，只读取当前切片指向的 TASK JSON。不要展开 blocked、waiting、replacement 或已核收冷历史；doctor 才做全历史机械体检。

* 默认不读日志、长报告、决策正文、其他部门正文、代码 diff 或完整测试证据；当前任务明确依赖时再读。

* 按任务、权威性和遗漏风险决定读取范围。只检查索引或元数据时，不得声称覆盖正文。

* 只有反复出现、确定、需要一致性的机械操作才写入脚本；语义检索、正文理解和证据取舍由 Agent 负责。

* 长期真值留在项目文件；会话只带当前工作集。

## 事实日志

日志默认不读，只记可核验轨迹；正式部门和临时外包分区保存，不写完整聊天或事后方法论。

只记录五类改变项目轨迹的事件：`MILESTONE`（可交付节点）、`CHANGE`（已确认的需求或边界变化）、`CORRECTION`（用户纠偏）、`DECISION`（关键方案选择）和 `INCIDENT`（值得倒查的失败或风险）。

普通回复、工具调用、重复确认和不改变边界的调整不记。变化/纠偏/决策立即记录，里程碑在节点完成时记录，事故只记影响判断或进度的事件。

写日志使用生成的 `agent_team_log.py append`，不先读取日志。脚本负责时间、事件 ID、周文件和原子追加，只返短收据。项目级事件由统筹部记，局部事件由发生部门记，其他部门只引用 ID；可复发的 `CORRECTION` 另写错题条目。

## 团队诊断

用户已说明交付物和会话模式时直接诊断，不重复追问。信息不足时一次只问一个关键问题：最终交付什么；会话由工具自动创建，还是用户手动创建窗口。

| 触发                                      | 建议角色                           |
| --------------------------------------- | ------------------------------ |
| App / Web / SaaS / Vibe Coding          | `lead,dev,test` |
| 资料收集、事实核验重                              | 加 `research`                   |
| 方案、排期、资源配置重                             | 加 `planning`                   |
| 数据、批处理、自动化重                             | 加 `data` / `auto`              |
| 内容或增长持续生产                               | 加 `content` / `growth`         |
| 隐私、权限、密钥、生产配置                           | 按风险节点加 `security`              |
| 成本敏感或有付费项                               | 按成本节点加 `finance`               |
| 无明显专用分工                                 | `lead,do,review`               |

非软件项目不要硬套产品、设计、开发、测试。缺少专用地基时，先问清目标、交付物、对象、验收、资源和风险，再由用户确认是否创建最小业务地基。

AI 产品不单独创建 AI 部门，也不按技术名词拆部门。生成岗位的操作真值是 `scaffold_team.py` 中的 `ROLE_DEFS`；本节只保留不可被项目覆盖削弱的职责合同：

* 产品部以用户需求、研究证据、资源约束和开发可行性建议为输入，负责完整产品规划及系统级技术路径、架构、模块/数据/接口边界、选型实验、依赖、迁移/回滚和实施阶段；输出 Spec、验收目标与系统 ADR，只写 `docs/decisions/system/` 等规划区，不写正式业务代码。产品选型实验必须是不可直接合并或发布的 disposable spike，采纳后由开发部重新实现和测试。
* 开发部以已确认 Spec、系统 ADR、设计和实施规划为输入，负责开工可行性复核、代码级决定、正式实现、自测与集成；只在 `docs/decisions/code/` 维护代码 ADR。发现系统合同不合理时提交证据与建议，经统筹退回产品部修订，不得静默改合同或路线。
* 系统 ADR 由产品部维护 `draft → proposed → accepted → superseded`；`accepted` 正文不可原地修改，实质变化必须新建 draft、重新评审确认，再把旧 ADR 标为 superseded。安全与测试只提交独立报告，不能直接改 Spec/ADR 或自证放行。

## 创建协作层

用户确认后运行：

```bash
python3 <skill目录>/scripts/scaffold_team.py "<项目目录>" \
  --profile "互联网 AI 产品 + UI + 质量关" \
  --roles "lead,dev,test" \
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

脚本生成协作文件与运行工具。缺 heartbeat、lease、查询、等待、恢复或归档适配器即为 `manual-degraded`：不轮询、不承诺无人值守或 Token 下降。

## 任务事务

* 派单、领取、阻断、等用户、恢复、候选绑定、gate verdict、完成和核收只通过 `agent_team_task.py`。普通 TASK 的 `claim / block / wait / resume / complete` 必须匹配所属部门当前已登记会话；换班后先 `rebind-owner`，doctor 会拒绝身份漂移。

* 执行状态只用 `queued / claimed / blocked / waiting_input / completed / acknowledged`；业务阶段写入 `domain_stage`；用户授权记录写入 `authorization_state` 和证据指针。`user_required / user_rejected` 禁止领取。

* `enqueue` 默认创建切片 owner，并用 `--required-gate test|security|...` 声明零到两个 gate。存在活动切片时拒绝第二 owner；固定候选后，gate 用同一 `--slice-id` 和 `--task-kind gate --gate-type ...` 创建，第三 gate 被拒绝。

* owner 用 `bind-candidate` 固定项目内候选 manifest 及 SHA-256。candidate ID 同时进入不随 100 条冷索引裁剪的永久账本，旧 ID 不能复用。下一代只接受当前代 gate FAIL，或统筹用 `record-user-exit --status needs_revision` 记录的当前候选用户修订；消费修订后仍保留审计记录，新代 gate 与用户出口重新开始，旧 PASS 不顶账。返工不新增 replacement owner TASK。gate 报告绑定当前 candidate ID 和报告 SHA-256；覆盖 final 报告后完成与 doctor 都失败。同一 gate 跨两代连续 FAIL 会原子冻结，用户修订不算 FAIL。

* 审核报告必须位于本部门 `报告/`，带任务一致的 YAML、`status=final`、`decision=pass|fail`、`candidate_id` 和非占位摘要。只有当前候选的全部 gate PASS、用户出口为 `verified / not_applicable` 后，gate 和 owner 才能依次完成、核收并进入冷历史。

* 统筹部只能核收 `completed` 任务；`acknowledged-by` 必须精确匹配会话状态中当前已登记的 `统筹部/会话ID`，用于防止普通部门误操作，核收后状态为 `acknowledged`。

* 用户冻结、任务堆积、上下文/存储压力、同一 gate 跨两代连续 FAIL 或无用户出口时，立即 `freeze-new-work`；冻结、TASK、换班、增删部门和升级共用项目控制锁。冻结只准安全停下、记录 verdict/用户出口/指标、完成、核收、清账、交接、换班和保全证据；返工、派单、恢复、扩编仍被拒绝，仅凭用户证据解冻。

* 1.5 返工只换候选，不用 `supersede`；拒绝/放弃时先 block/wait，再 `resolve`。升级后的普通 1.4 TASK 只能清账、核收或 `--include-cold` 审计，禁止重新 claim/resume/complete；legacy temporary 也只能在 frozen、无活动切片时走专用恢复与收口白名单，完全终态前不得解冻或创建 1.5 owner。

* `enqueue / authorize / resolve / ack / record-user-exit / record-metrics / set-notification` 的 actor 必须匹配已登记统筹会话。`block / wait / resume / record-user-exit` 完全相同的重试返回零写入 NOOP；目标状态、原因、证据、actor 或候选代次冲突时失败。actor 是防误操作和审计绑定，不是操作系统级认证。

* 测试遵守前述用户影响门；有界面任务先冒烟和安全探针，体验确认后回归；无界面任务按验收出口验证。

## 用户闸门与汇报

统筹不穷举场景，按用户意图、对当前 TASK 的影响，以及是否需用户独有信息、亲自体验/判断或授权分流：需则用户出口保持 `pending` 并停下；无依赖的纯代码/内部检查过自检和所需 gate 后记为 `not_applicable`，同一切片内继续，不开新切片或跳审核。临时提问/状态追问直接答并保留当前 TASK；同范围反馈续做，含义或实质影响不清再问。

统筹提出恢复、绑定候选等状态动作前，先运行只读 `next-action --task-id ...`。协议拒绝时只汇报第一阻断、当前允许动作和是否需要用户决定；不得擅自把局部协议拒绝扩张成修改 Skill、项目业务或发布流程。

正式汇报用于体验、信息、选择、风险或阶段收口，稳定保留`结果`和`需要你做什么`，真实风险再写`还需注意`；未验出口写“当前不可确认可用”，体验给入口/操作顺序/预期结果/重点判断/已知限制。普通问答自然回复，不为凑格式制造空话；默认不展开 TASK ID、状态词、哈希、命令、日志或协议。

## 单 TASK 临时外包（按需）

用户主动提出临时外包时才读取参考文件。1.5 新建会返回 `TEMPORARY_EXECUTOR_P2_REQUIRED`；只允许按参考文件收口旧任务，不得用普通 enqueue 或隐式子会话绕过单 owner。

## 会话模式与换班

* `manual`：用户建窗口并发送上岗引导后，也必须用同一会话 ID 依次登记 `created / onboarded / registered`；统筹部 registered 后才能派单。不得把生成文件说成已创建会话。

* `auto`：生成文件后，用当前环境的会话工具创建各部门新会话、发送上岗引导，再把真实会话 ID 和外部证据写入会话状态；会话工具刷新部门表派生索引。任一步失败都如实回退为人工。

* 自动模式的每次外部调用后立即用 `agent_team_session.py` 记录 `created / onboarded / registered / failed`；重试先读状态，不重复创建已成功会话。

* 项目文件存长期真值，会话只留当前工作集。统筹会话按项目阶段，执行会话按端到端切片；同一切片返工续用原会话，切片收口且下一项明显不同才建议换班。审核不继承执行长上下文，同一候选的审核复测可续用原会话。

* 旧候选或身份串入、无关材料重复读取、压缩后很快再失焦是强信号。有可比宿主数据时，只比较同模型/推理/Skill/插件/附件下的输入、缓存输入、输出和工具调用；否则写“无法测量”，不用轮数、文件字节、响应时长或固定倍数阈值判断 Token。

* 只建议换班并说明证据、收益和在办事项；用户未明确同意时不创建、登记或归档。

* 用户说“换会话 / 切换会话 / 换班”即授权同部门换班：旧会话更新交接和必要日志后创建新会话，发送四文档路径；新会话接班并登记新 ID 后才归档旧会话。

* `begin-switch` 后保留新旧 ID 到取得精确归档回执。新 ID 未产生可 `restore-old`；已登记则先归档新会话并留回执。归档失败时不覆盖 thread ID。会话 ID 必须无空白、不以 `=` 开头且不超过 300 字符。

* 不使用会复制旧历史的 fork。创建、发送、接班、登记或归档失败时保留旧会话。

## 用户常用口令

`接班` 恢复职责与当前任务；`先接班，不要开始任务` 汇报后停下；`交班` 更新交接和必要日志；`换班 / 换会话` 授权创建同部门新会话，新会话登记成功后才归档旧会话。

## 维护与验证

* 增加部门：`--add-roles "<ids>"` 同一事务更新真值与派生表；活跃部门重复添加保持幂等，已停用部门会复用原身份重新启用。

* 当前运行协议为 `1.5.1`。跨协议升级先由统筹冻结，再用 `--upgrade-collaboration`；1.4 TASK 保持原字节并作为冷历史。1.5.0 活动切片原位补齐用户修订账本，TASK、候选、旧 PASS 与报告保持原字节；首次 1.5.1 状态写入前可按本代 manifest 回滚，写入后失败关闭。更早协议仍只在无活动切片且没有新协议 TASK 时允许回滚。

* 停用部门：收口任务；已登记会话取得真实归档回执后先运行 `agent_team_session.py retire`，再运行 `--deactivate-roles ... --deactivation-evidence ...`。保留历史身份并维持三层结构。

* 部门表丢失或损坏时运行 `agent_team_session.py rebuild-registry`，只从会话状态真值重建。

* 通知能力只在上岗/接班时登记一次；后续按部门表的自动/人工模式执行。

* 修改本 Skill 后运行项目验证器、`quick_validate.py`、Python 编译和 diff 检查。同步全局安装、发布或逐项目升级都是独立授权动作；源码候选 PASS 不能冒充已安装、已发布或 Lulu 可用。
