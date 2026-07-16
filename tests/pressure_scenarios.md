# Agent Team 对抗场景

> 自动场景由 `scripts/verify_agent_team.py` 黑盒执行；涉及语义判断的场景按本清单人工复核。

## 1. 授权闸

`user_required` 与 `user_rejected` 任务不能领取、恢复或完成。变更授权必须附证据指针。工具明确说明它保存的是审计声明，不冒充用户身份认证。

## 2. 单一在办任务

同部门只能有一条 `claimed`。一条任务进入 `blocked` 或 `waiting_input` 后，可领取另一条独立任务；恢复旧任务前必须先结束当前 `claimed`。

## 3. 稳定任务路径

TASK 文件固定为 `tasks/TASK-*.json`，状态只在 `execution_state`。领取、阻断、完成和核收后原路径仍可用，收件箱链接也不失效。

## 4. 完成收据的真实边界

`complete` 拒绝不存在、越界、经过符号链接的本地产物，也拒绝把项目根目录当产物。外部产物必须显式声明。返回 `TASK_STATE_OK` 时只声称状态和路径检查，不声称业务质量。

审核层任务缺少本部门审核报告、报告未作为产物提交、YAML 缺字段或任务 ID 不一致时必须拒绝完成。非统筹标记不得执行 `ack`。

## 5. 损坏真值

任一 TASK JSON 格式损坏、ID 与文件名冲突、状态非法、字段缺失或类型错误时，所有任务写操作在突变前停止。删除 `authorization_state` 后不得把任务当成 `none`；删除 `department` 后再派单不得新增任务。不得把损坏任务忽略成“索引暂时过期”。

## 6. 收件箱损坏

收件箱缺少受管标记时，任务 JSON 仍可安全落盘并返回 `TASK_INDEX_STALE`。修复后用 `rebuild-index` 明确重建，不要求手工复制任务正文。

## 7. 稀疏日志与路径攻击

初始协作层不生成空周日志。第一条真实事件才创建周文件。日志脚本拒绝符号链接和硬链接，不允许通过项目内路径改写项目外文件。

## 8. 会话状态重试

每个 `created / onboarded / registered / failed` 转换要求证据。失败重试只能从上次成功点继续，不能跨级。部门表是派生索引，会话 JSON 是真值。

初始通知模式必须来自协作层的 `auto / manual`，用户确认改变后通过 `set-notification` 写入会话 JSON 并刷新部门表，不能手工改索引。

## 9. 同部门换班

会话变重只产生带原因的换班建议。用户授权后才 `begin-switch`。新会话依次登记 created、onboarded、registered，旧会话归档后才 `finish-switch`。新 thread ID 尚未产生时，中途失败可直接用带原因的 `restore-old`；一旦新 ID 已登记，`restore-old` 必须精确验证新会话的归档回执后才能恢复旧会话。旧会话归档失败时保留新旧两个 ID 并重试，不能启动第二次换班覆盖 `previous_thread_id`。

## 10. 已有任务的接班

新会话读完入口后，若存在授权清楚且无冲突的 `claimed` 任务，先短报再同一轮续做。无任务、授权不明、边界冲突或用户明确只要求接班时才停下。

## 11. 正文读取策略

Skill 只负责把 Agent 路由到部门入口、TASK JSON 和相关项目文件。它不强制全文、局部读取、固定篇数或特定检索工具。Agent 只查看 YAML 或索引时，不能声称已经覆盖正文。

## 12. 通用审核

通用项目的审核模板只要求审核对象、标准、独立证据、失败路径、反向探针、用户出口（如适用）、未覆盖项和结论。不得强行出现 engine、worker、UI、打包态等软件字段。

## 13. 可选设计预览

用户没提预览且任务没列为交付物时，不额外制作。用户明确要看时，预览必须可直接看到，尽量与实现同源，并明确它是设计意图及保真差距。

## 14. 旧协议升级

升级在任何写入和备份目录创建前，先对已平铺任务和旧状态目录任务做完整结构预检。通过后才备份受管文件，把旧版状态目录任务迁移到平铺稳定路径，移除旧读取器与规则，并刷新项目 `docs/agent-guide.md` 的受管协议版本。任务损坏、目标冲突或目录不安全时，升级保持协议、运行脚本和任务真值不变。

## 15. 同版本运行时缺失

协议版本相同但模板、脚本或索引文件缺失时，`--upgrade-collaboration` 必须修复，不能返回无需升级。运行时不完整时，`--add-roles` 也不能把无操作当成功。

## 16. 升级路径越界

`scripts/`、`升级备份/`、`tasks/` 或部门目录被替换为符号链接时，任何升级和增量操作在写入前停止，项目外目录保持不变。

## 17. 重复规则控制

规范性协议只在协作层 README、任务交接协议和会话启动清单保留。项目 agent-guide 只提供入口指针；报告模板只共享一份；部门目录不生成重复 README 和空日志。

## 18. AI 产品不按技术拆部门

新建 AI 产品团队时，`ai` 角色必须被拒绝，不生成独立 AI 工程部。产品部负责完整规划和 AI 行为验收目标；开发部负责模型接入、Prompt、RAG、Agent、评测、成本、延迟和降级等全部技术实现。旧协作层若仍有 AI 工程部，升级必须停止并要求用户先授权合并，不能静默删除历史。

## 19. 地基语义与机械检查分层

新建协作层前，Agent 亲自阅读并复核地基是否真正说清目标、交付范围和验收标准，再显式传入 `--foundation-file`。脚本只检查相对路径、项目边界、符号链接、UTF-8、文件类型、非空和大小上限，不扫描整个 `docs/`，不用关键词、字数或字符种类冒充语义判断。未传地基文件、越界、符号链接或非 `docs/spec.md` 且未经用户确认时，必须在创建协作层前停止。

## 20. 临时外包必须由用户发起

缺少 `user_confirmed` 和授权证据时，不能创建临时执行者。重复 client key 必须返回原资源，不能重复创建 workspace 或会话；不同 client key 不能接管已有临时执行者。

## 21. 通用父部门

临时执行者必须使用 TASK 已登记的父部门，日志也只能进入该父部门临时板块。数据模型和用户文案不能把开发部写成永久默认值；开发只作为首轮 Git 交付样板。

## 22. 并行影响声明

正式在办任务缺少影响声明时只能返回 `manual`。写路径父子重叠、共享契约重叠或真实外部副作用必须拒绝。主工作区脏但与临时写路径独立时可以开发，不能因此提前进入 main；工具不得自动 stash、reset 或 checkpoint。

## 23. 临时规则与唯一控制根

workspace 只能位于 `.agent-team/workspaces/TASK-ID/`，必须有 ownership marker 和唯一临时规则。临时分支修改 `docs/collaboration/`、越出 write paths、包含未提交文件或规则 digest 未确认时，候选、submit 或清理必须停止。

## 24. 候选证据失效

brief 实质 amend 必须带 expected revision 并重新进行并行判断。新候选产生后，旧用户确认、独立审查和测试证据不得自动沿用。审查失败的候选不能 submit。

## 25. 合并后只做一次正式验证

外包自测和可选独立审查后，正式部门只验证准备成为正式结果的完整 tree。main 漂移、候选修改、tested tree 不一致、主工作区存在未解释产品改动时，晋升必须停止。进入 main 的 tree 必须等于测试证据记录的 tree。

## 26. 吸收与清理

成果 integrated 后仍不能立即清理。所属正式部门和项目全局知识必须完成或明确不适用，最终吸收关关闭后才能删除普通 workspace 和分支；delivery 保护 ref 继续保留。长期无回复只 standby，用户未明确 abandoned 时不得清理。

## 27. Amend 与规则确认

实质 amend 和正式返工必须增加 attempt，清空 candidate、review、delivery、integration 和旧测试证据，重生成临时规则并清空确认时间。新 digest 未由临时会话确认时，candidate 和 submit 都必须拒绝。已 submit 或统筹接管后不能绕过 rework 直接 amend。若上一 attempt 已完成前置清点，rework 必须将当前吸收状态和收据归零，只保留标记旧 attempt 的失效历史快照。

## 28. Delivery 留证

清理前必须读取并核对 delivery 保护 ref、commit 和 tree。删除或漂移保护 ref 后，integrated 与 abandoned 两条清理路径都必须拒绝；Git GC 后仍要能通过保护 ref 读取交付证据。

## 29. 日志身份真值

临时日志参数必须与权威 TASK 中的 executor ID、parent department 和 temporary executor 绑定完全一致。普通 TASK、伪造 executor 或伪造父部门都不能写入临时板块。

## 30. 正式测试证据

`record-integration-test` 不能接受纯文字 pass。它必须引用已完成并由统筹核收的审核层 TASK、本部门正式报告和当前临时 TASK 指针；报告必须绑定 tested commit/tree 和结论。自动验证应实际运行候选测试后再生成报告。

## 31. 半失败恢复

provision、promotion 和 cleanup 都记录 planned、started、succeeded、verified 轨迹。Git ref 已改变但 TASK 未更新，或资源已删除但 TASK 未归档时，reconcile 必须根据真实 ref、worktree 注册、完整 ownership marker 和保护证据恢复；存在一半资源时保留现场并转人工。

cleanup 成功或 reconcile 确认资源已移除后，`promotion_state` 可以进入 `archived`，但存在真实 thread ID 时 `temporary_session` 必须继续保持 `standby` 并返回该 ID。AI 当前有归档工具时直接执行并记录绑定 thread ID 的成功收据；没有时提醒用户归档具体会话并在完成后告诉 AI，不设置固定口令或全流程硬锁。用户未确认前不得声称已归档，但其他不依赖该归档结果的安全工作可以继续；之后收到明确确认即可登记。错误 thread ID、缺少 `archived=true` 或既无 host 也无 user_confirmation 的回执必须拒绝。从未创建真实会话的 abandoned 路径返回无需归档并收口为 `cancelled`。已交付任务在 cleanup 崩溃恢复时若缺失 thread ID，必须转人工核对。

## 32. 首轮适配诚实边界

数据模型与 preflight 支持任意父部门，但当前完整 workspace、candidate、delivery、tested-tree 和 main 晋升链只开放临时开发外包。设计、研究等专业候选和权威吸收适配完成前，不得声称可完整执行。

## 33. 用户确认绑定候选

用户验收必须记录当前 candidate revision 和 tree digest。workspace 新增 commit 并固定新候选后，验收状态退回 pending；旧证据不能由 candidate 命令自动改写 revision 后继续 submit。

## 34. 吸收顺序

前置清点只能在统筹接管 delivery 后完成。所属部门、项目全局和最终吸收只能在正式验证通过并 integrated 后完成；submitted、reviewing 或 ready 状态不能提前关闭最终吸收关。

## 35. 旧 TASK 深度升级预检

旧版 TASK 的 temporary executor、impact、workspace、rule、session、acceptance、candidate、review、delivery、integration、operation 和 absorption 必须在备份与写入前深度验证。operation 还要校验状态枚举、资源元素、历史事件结构，以及当前状态与历史末项一致性。允许安全补齐新字段，但 executor type、父部门、状态、候选绑定或嵌套结构损坏时升级保持原协议和真值不变。任何旧版本中能通过当前严格解析，精确绑定真实 thread ID、`archived=true` 与 host 或 user confirmation 来源的有效回执都继续保留；缺失、冲突或无法验证的回执必须先备份，再退回 `standby` 并返回真实 thread ID。已经完成资源清理但仍在 `standby` 的旧任务，升级时重新返回归档动作，但不改写事实或形成全流程硬锁。

## 36. 正式报告只认 frontmatter

正式测试证据只解析文档开头的 YAML frontmatter，并精确核对 type、department、target、status、related task、decision、tested commit/tree 和 result。正文里出现一组看似正确的子串，不能覆盖 frontmatter 中的失败或错误版本；frontmatter 重复字段同样拒绝。

## 37. 固定候选与 workspace HEAD

候选固定后，只要 workspace 又产生 commit，即使工作区干净，旧候选也不能 submit。必须重新固定候选，并重新绑定用户确认与可选审查。

## 38. 不可逆状态与可收口取消

`integrated / archived / cancelled` 不能被 `abandon` 改写。真实会话进入 `standby` 后，外部 `session-mark --state cancelled` 必须拒绝，避免绕过资源核对形成无法 reconcile 的死角；从未产生 thread ID 的 abandoned 清理可以在资源验证完成后由清理事务内部收口为 `cancelled`。

## 39. thread ID 全局唯一

正式部门之间、正式部门与临时 TASK 之间、两个临时 TASK 之间都不能复用同一个真实 thread ID。任何一侧后登记时都必须读取全局真相并在写入前拒绝。

## 40. 影响范围双向冲突

正式任务先声明、临时任务后 preflight 时要查冲突；临时任务已经 provision 后，正式任务再 `declare-impact` 也要反向检查写路径、共享契约和外部副作用。拒绝时不得改变正式 TASK revision 或旧声明。

## 41. 路径与 JSON 严格性

TASK 文件的 `tasks/` 父目录若被替换为指向项目外的符号链接，所有任务写操作必须在创建外部文件前拒绝。任务、会话、协议、operation 和 ownership 等受控 JSON 一律拒绝重复 key，不能采用“最后一个值覆盖前值”的宽松解析。

## 42. 1.4.6 受管升级

`1.4.5 → 1.4.6` 升级保留 created、onboarded、registered、previous thread、evidence、notification 和进行中的 operation 真相。同版本受管脚本内容漂移时按 manifest 修复；每个部门四份入口文档与根级 `错题集.md` 中任一缺失都能被完整性检查发现，并由显式升级补回，已有错题集、交接和收件箱正文不被覆盖。

## 43. 待归档动作可重复读取

`pending-archives` 是无写锁、无突变的只读查询，相同真相重复查询得到相同 `ARCHIVE_THREAD_REQUIRED`。same-version 升级也要重放尚未取得真实收据的人工归档提醒；登记有效 archived 收据后查询返回无待办。

## 44. 换班归档证据

`finish-switch` 只接受精确绑定 `previous_thread_id`、包含独立 `archived=true` 字段，并注明 host 或 user confirmation 的收据。普通文字、相似前缀 ID、冲突字段和 false 值都不能清空旧会话真相。

## 45. 默认权限与安装副本

验证器在系统临时目录生成编译缓存，不要求改写 `HOME`，可在 Codex 默认工作区写权限下运行。安装副本只能包含 `SKILL.md`、`agents/openai.yaml`、`scripts/scaffold_team.py`、`scripts/temporary_executor_runtime.py`，且四个文件必须与仓库逐字节一致；多一个开发文件或任一内容漂移都失败。CI 对所有 push 与 pull request 执行这些检查。

## 46. 恢复与返工重做准入

临时 TASK 因 amend 后的写路径或共享契约冲突进入 `blocked` 时，`resume` 和 `rework` 都必须在同一把任务锁内重新扫描当前影响。冲突仍在时保持 blocked，不增加 attempt，不恢复 claimed。正式 TASK 从 blocked/waiting 恢复时也要重查活跃临时影响，不能只在第一次 claim 时检查。

## 47. 增量部门与全新搭建收敛

同一天、同一 profile 和同一最终 roles 下，“一次搭建全部部门”与“先搭建再 `--add-roles`”生成的部门表、会话启动清单、路由表和会话状态必须逐字节一致。重复新增已存在部门只返回幂等跳过，不改写已收敛真值。

## 48. 升级回滚保留目录真值

旧版 `tasks/queued|claimed|.../` 在迁移后、新受管文件落盘前故障时，回滚必须恢复原任务路径、原本存在的空状态目录和每个目录的权限。`rollback-manifest.json` 必须记录这些子目录的 existed 与 mode；丢目录或用默认 0755 替代原权限都不得声称“逐项恢复”。

## 49. 会话 ID 精确性与缺失态

归档收据中的 `thread_id` 按原始字符串精确比较，大小写不同也必须拒绝；只有 key 和 `archived=true` 布尔语义可做大小写归一。资源已验证清理且 `promotion_state=archived` 时，存在 thread ID 只能是 standby/archived；缺少 thread ID 只能是“从未创建真实会话”的 cancelled。已清理+standby+空 ID 必须拒绝升级和查询，不能沉默消失。

## 50. 提交、集成与清理终态不可回退

delivery submit 后，`candidate` 和 `review` 不得继续改写证据；只有显式 `rework` 可生成新 attempt。`record-integration-test` 不得把 integrated 候选退回 ready/reviewing。已验证的 integrated promotion 重试 reconcile 只返回幂等收据且 TASK 字节不变；已清理的 archived TASK 不得被 review 或 promotion reconcile 重开，`pending-archives` 仍必须保留待归档会话。

## 51. 会话身份不可覆盖

正式换班中，新会话进入 created/onboarded/registered 后，无精确归档回执的 `restore-old` 必须原子拒绝；待归档旧 ID 存在时也不得再次 `begin-switch`。临时会话一旦登记真实 thread ID，failed 重试、规则重建和重新 active 都只能使用原始字符串完全相同的 ID。外部 `session-mark --state cancelled` 不能取消任何真实会话；`cancelled` 只由无 thread ID 的 abandoned cleanup 在资源验证完成后内部收口。

## 52. 归档回执可表示性

正式和临时 thread ID 都不得包含空白、以 `=` 开头或超过 300 字符，避免真实身份无法放进结构化归档回执。正式 `finish-switch` 的精确旧会话收据必须持久化到 evidence；之后切换通知模式只能更新通知说明，不能删除归档事实。

## 53. 未决事务冻结

workspace 创建事务处于 started 或 failed 且尚未 reconcile 时，resume、rework 和 abandon 必须原子拒绝。promotion 处于 planned、started、succeeded 或尚未 reconcile 的 failed 时，重新测试、晋升、返工、放弃和 preflight 知识吸收都不得继续；只有 reconcile 确认 main 未前进后写入的 failed 才能重新进入正常生命周期。cleanup 进入 started 或尚未 reconcile 的 failed 后，知识吸收等其他写操作必须冻结，不能在资源实际状态未判定时改写终态证据。

## 54. 创建失败仍保留真实会话

workspace 创建事务 verified 后，临时会话在 provisioning 阶段已经取得真实 thread ID、但后续上岗失败时，`session-mark --state failed --thread-id <真实ID>` 必须保存该 ID。后续 active 只能复用同一原始 ID；最终 abandoned cleanup 仍返回 `ARCHIVE_THREAD_REQUIRED:<真实ID>`，不得把它降格为从未创建会话的 cancelled。创建事务尚未 verified 时必须先 reconcile，不得提前登记会话后再被 reset 丢失。

## 自动复验

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m py_compile scripts/scaffold_team.py scripts/temporary_executor_runtime.py scripts/verify_agent_team.py
python3 scripts/verify_agent_team.py
python3 scripts/verify_agent_team.py --check-installed-copy "$HOME/.codex/skills/agent-team"
python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" .
```

修改运行时后，还必须用全局安装副本覆盖 `AGENT_TEAM_SCAFFOLD` 再跑验证器。
