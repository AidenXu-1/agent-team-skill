# Agent-Team 2.1 协作协议规格

## 目标与用户需求

让一个长期项目在多会话协作时保持三件事：当前工作足够小，责任身份可核验，任何局部检查都不能冒充最终可用。

本轮针对 Lulu 深度使用暴露出的八类问题：任务持续膨胀、历史进入热上下文、Token 消耗失控、普通 TASK 身份漂移、局部 PASS 冒充可用、返工生成重复候选、冻结存在竞态、候选与迁移缺少唯一且可恢复的身份。

## 2.1 最小运行模型

1. 一个项目同一时刻最多一个活动切片。
2. 一个切片只有一个执行 owner；owner 在专业 gate 和用户出口完成前保持责任，不因返工另开替代 TASK。
3. 一个切片最多两个专业 gate。gate 的 FAIL 追加到同一 gate TASK；同一 gate 跨两个候选代次连续 FAIL 时原子冻结新工作。
4. 一个切片同一代只有一个候选身份。owner 固定候选清单及 SHA-256；gate 必须审查同一候选。永久候选 ID 账本不随 100 条冷索引裁剪，旧 ID 终身不可复用。
5. 普通 TASK 的 claim、block、wait、resume、complete 必须绑定该部门当前已登记会话。换班后先留下可审计的 ownership rebind，再继续动作。
6. 收件箱只呈现活动切片；完整 TASK JSON 与日志保留为冷历史。`onboard-bundle` 只从活动切片和受管 legacy 窄索引确定本部门 TASK，不扫描人工文字；冻结恢复任务单列。超过 24,000 热字节时告警但不截断，过期或并发变化时失败关闭。
7. 冻结、TASK 变更、会话换班、增删部门和协议升级共用同一项目控制锁。冻结历史采用有界保留，不能在第 1001 次把控制文件写成下一次无法读取的状态。
8. 没有宿主 heartbeat、lease、状态查询、等待、恢复和归档适配器时，运行模式明确记录为 `manual-degraded`；不得承诺无人值守持续执行或宣称 Token 已下降。

## 协议与迁移边界

- Skill 候选版本：2.1.0。
- 生成协议：1.5.0。
- 1.4.x TASK 在迁移过程中保持原字节与冷历史语义；升级后禁止重新领取/恢复/完成，只可清账、核收或显式审计。仍在 claimed 的旧 TASK 必须先安全停下，否则升级拒绝。缺精确归档收据的 archived legacy temporary 不改写原 TASK，只在派生恢复账本登记待办与新收据。
- 未终态 legacy temporary 只能在 frozen 且没有 1.5 活动切片时收口；迁移生成窄 `legacy-closeout-index`，由协议受管 SHA-256 锚定，并永久标记必须补归档收据的 TASK。日常热路径只读取索引指向的旧任务，不扫描全部冷历史；索引截断、回滚或必需 recovery 条目缺失都失败关闭。完全归档并核收前拒绝解冻、enqueue 和 claim，doctor 对双 owner 风险失败关闭。
- 升级先冻结并生成可校验回滚清单。热索引重建完成后才绑定最终 operation、rollback manifest 和可回滚状态摘要；人工交接内容、升级新建目录里的额外文件、旧代清单、其他现场变化、1.5 TASK 或活动切片都会在任何回滚写入前被拒绝。
- 不自动迁移、展开或重写 Lulu 的 936 个历史 TASK；逐项目升级必须单独授权。

## 验收标准

- 伪造或过期部门会话无法领取、恢复、阻断、等待或完成普通 TASK；doctor 能发现身份漂移。
- 冻结与 enqueue、add-role、upgrade 并发时至多一个动作成功，冻结后不会产生新工作。
- 第 1000、1001 次冻结切换后控制文件仍可读取、可 doctor、可继续切换。
- 新切片无法创建第二 owner 或第三 gate；返工不创建 owner replacement TASK。
- gate 对错误候选的结论被拒绝；连续两次 FAIL 后冻结，且 TASK、候选和冻结事实保持一致。
- gate verdict 固定 final 报告 SHA-256；报告被覆盖或篡改后，完成与 doctor 都失败关闭。
- 零 gate 切片的冷历史固定候选 manifest 路径和 SHA-256；切片关闭后覆盖 manifest，doctor 仍失败关闭。
- 热交接或收件箱被人为改旧后 bundled onboarding 失败；人工文字里的假 TASK 或跨部门 TASK 不能注入接班身份；重建后恢复。成功输出不生成第五份摘要、不写业务文件、不展开冷历史。
- 活动入口不展示冷历史 blocked/waiting/replacement 链。
- 没有宿主适配器时输出 manual-degraded，不运行轮询，不给 Token 优化结论；有真实 adapter 指标时才记录输入、输出、最大输入、工具调用、轮询与热上下文字节。
- 升级失败可原子恢复；上一代 rollback manifest、人工交接内容、升级新建目录子项、其他回滚表面状态变化或新 1.5 TASK 出现后均失败关闭，预检失败不得留下半回滚协议或恢复循环。
- 切片最后一次 resolve/ack 与 `active_slice` 收口使用 WAL；崩溃后自动收敛，不能留下“TASK 已终态但切片永久占用”的夹层。
- `list` 默认只显示当前切片；只有主动传 `--include-cold` 才输出冷历史。
- 完整验证、快速 Skill 校验、Python 编译和 diff 格式检查全部通过；这些 PASS 仍不代表 Lulu 可用、已发布或已升级。

## 本轮禁止事项

不修改 Lulu，不同步全局 Skill，不创建 Release，不安装或启动 App，不操作 Keychain、安装器或用户数据。
