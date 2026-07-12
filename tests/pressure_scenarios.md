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

会话变重只产生带原因的换班建议。用户授权后才 `begin-switch`。新会话依次登记 created、onboarded、registered，旧会话归档后才 `finish-switch`；中途失败用带原因的 `restore-old`。

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

## 32. 首轮适配诚实边界

数据模型与 preflight 支持任意父部门，但当前完整 workspace、candidate、delivery、tested-tree 和 main 晋升链只开放临时开发外包。设计、研究等专业候选和权威吸收适配完成前，不得声称可完整执行。

## 33. 用户确认绑定候选

用户验收必须记录当前 candidate revision 和 tree digest。workspace 新增 commit 并固定新候选后，验收状态退回 pending；旧证据不能由 candidate 命令自动改写 revision 后继续 submit。

## 34. 吸收顺序

前置清点只能在统筹接管 delivery 后完成。所属部门、项目全局和最终吸收只能在正式验证通过并 integrated 后完成；submitted、reviewing 或 ready 状态不能提前关闭最终吸收关。

## 35. 旧 TASK 深度升级预检

1.4.0 TASK 的 temporary executor、impact、workspace、rule、session、acceptance、candidate、review、delivery、integration、operation 和 absorption 必须在备份与写入前深度验证。operation 还要校验状态枚举、资源元素、历史事件结构，以及当前状态与历史末项一致性。允许安全补齐 1.4.1 新字段，但 executor type、父部门、状态、候选绑定或嵌套结构损坏时升级保持原协议和真值不变。

## 自动复验

```bash
python3 -m py_compile scripts/scaffold_team.py scripts/temporary_executor_runtime.py scripts/verify_agent_team.py
python3 scripts/verify_agent_team.py
python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" .
```

修改运行时后，还必须用全局安装副本覆盖 `AGENT_TEAM_SCAFFOLD` 再跑验证器。
