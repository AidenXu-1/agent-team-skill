---
title: Agent Team 单 TASK 临时外包运行参考
status: implemented
---

# 单 TASK 临时外包运行参考

> 仅在用户主动提出临时外包、并行外包或 TASK 级临时会话时读取。适用于运行协议 `1.4.6`。

## 启用边界

- 统筹部只能在用户主动发起后判断和创建，不能主动扩容、拆任务、自动重派或让临时执行者自行领取共享任务。
- 每个临时执行者固定为 `temporary_executor + parent_department + 单一 TASK`。用户侧按父部门称“临时开发外包”“临时设计外包”等。
- 当前完整 workspace、候选、delivery、tested-tree 和晋升链只开放临时开发外包。其他父部门只能做通用 preflight，专业版本和吸收方式未适配前不得进入执行链。
- 首轮禁止密钥、隐私数据、生产、发布、购买、付费、真实发送及其他外部副作用。Git worktree 只提供协作隔离，不是 OS 权限沙箱。

## 用户表达与授权

- 用户只询问“能不能并行 / 是否适合外包”时，只做判断；判断通过后仍需确认创建，不能把问题当成授权。
- 用户明确表达“如果可以就帮我开 / 适合的话直接创建”时，才同时包含判断与创建委托；判断通过后可以直接创建。
- 授权只绑定当前 TASK、范围和证据指针。“建议下一步”、方向满意或允许继续讨论都不扩大权限。
- 用户说“取消 / 先停一下”只停止继续投入，保留 TASK、workspace 和证据，不自动等于 `abandoned`，也不允许 cleanup。
- 只有用户明确表示放弃该成果，才能进入 abandoned 收口。长期无回复只进入 `standby`，仍绑定原 TASK 和 workspace。

## 创建前预检

1. 正式在办、阻断、等待输入以及尚未吸收完成的临时任务都声明 `write_paths / shared_contracts / external_effects / base_revision / owner_task`。
2. 声明不足只报 `manual`。写路径、共享契约、生成物或外部影响重叠时阻断；工具不得自动 stash、reset、commit 或 checkpoint 正式部门工作。
3. workspace 固定为项目内 `.agent-team/workspaces/TASK-ID/`，项目根必须忽略 `/.agent-team/`。创建前另查 watcher、构建扫描和同步工具，不能把 Git ignore 当成全部隔离证据。
4. `docs/collaboration/` 的权威副本只在主控制根。linked worktree 中的副本没有权威性；临时分支修改协作层时不得提交或晋升。
5. 通过预检后才创建 TASK、workspace、ownership marker、临时规则和真实会话。重复 client key 必须绑定同一规范化请求，不能凭名称猜测已有资源。

## 必须暂停并升级统筹部

任务内部、可逆且不扩大授权边界的调整可以继续；以下变化必须暂停：

- 修改 Spec、需求边界、验收合同、共享 API、Schema 或数据契约。
- 扩大写入范围、引入新依赖，或触及数据库、迁移、认证、权限、密钥、隐私。
- 增加付费、生产、发布、购买、真实发送等外部副作用。
- 与正式任务假设冲突，或当前需求已经实质变成另一项任务。

## 临时会话入口与阅读

workspace 内只生成 `.agent-team/临时执行规则.md`，它是临时会话的身份、权限、日志和收口入口。

临时会话按 TASK 指针读取 Spec、相关 ADR、conventions、代码、测试和必要证据；默认不读父部门完整岗位说明、收件箱、交接、progress 和长期报告。专业标准可以继承，组织身份和正式权限必须重写。

临时日志写入父部门周文件的“临时外包日志”板块，必须精确绑定 TASK、executor ID 和 parent department。只记录五类轨迹事件，不复制聊天或任务正文。

## 正向生命周期

1. 用 `agent_team_temporary.py` 完成影响声明、preflight 和 provision；真实会话创建后登记原始 thread ID 并确认当前临时规则 digest。
2. 任务内部的小调整可继续。实质变化使用带 expected brief revision 的 `amend`，原子重做并行判断、增加 attempt、清空候选与交付证据并重生成规则。
3. 已 submit 或统筹已接管时必须先 `rework`，不能直接 amend。`blocked / waiting_input` 的 `resume` 或 `rework` 都要重做当前影响准入，冲突未消失时保持阻断。
4. 先固定可复查候选，再把 `confirmed / delegated / not_applicable` 绑定到 candidate revision 和 tree digest。workspace 新增 commit 后必须固定新候选并重新确认。
5. 独立子 Agent 审查只在用户要求时调用；审查者只给结论和证据，不直接修改。修复由临时执行者完成，并形成新候选。
6. `submit` 固定 delivery 并用受保护 ref 留证，临时会话进入 `standby`。submit 后不得继续改写 candidate 或 review；需要修改时显式 rework。
7. 统筹部核收后，正式体系只验证一次“准备成为正式结果的完整候选”。delivery 与目标 tree 相同时直接验证；main 漂移、冲突或集成修改时才形成候选集成态。
8. 正式通过必须绑定已完成且由统筹核收的审核层 TASK、本部门真实报告、tested commit/tree、结论和未覆盖项。报告证据只认文档开头 YAML frontmatter；正文相似文字不能替代。进入 main 的 tree 必须等于已测试 tree。
9. 晋升后分别完成成果吸收和知识吸收。知识默认只回到所属正式部门，以及项目 Spec、ADR、conventions、progress 或错题集；其他部门仅在明确受影响时介入。临时执行者只能提出待吸收候选，不能自行把判断写成正式 Spec、ADR、conventions 或正式报告。
10. 只有成果已正式吸收，或用户明确放弃且必要证据已保留，才能 cleanup。

## 状态与不可回退事实

- `execution_state` 管任务占用：`queued / claimed / blocked / waiting_input / completed / acknowledged`。
- `user_acceptance` 管用户确认：`pending / confirmed / rejected / delegated / not_applicable`。
- `promotion_state` 管交付晋升：`not_submitted / submitted / reviewing / waiting_base / ready / integrated / archived / cancelled / abandoned`。
- `temporary_session` 管真实会话：`provisioning / active / standby / archived / failed / cancelled`。

`completed` 只表示 delivery 已冻结；`acknowledged` 表示统筹接管；`integrated` 表示成果进入正式权威载体；`promotion_state=archived` 表示成果、知识和临时 Git 资源已收口。它们都不能冒充真实会话已经归档。

已 integrated、资源已清理或会话已归档的机械事实不能被补录命令回退。新 attempt 使旧 candidate、用户确认、review、delivery、integration、测试和吸收收据失效，只保留历史快照供倒查。

## 半失败与 reconcile

- provision、promotion 和 cleanup 都记录 operation ID、幂等 key、请求 digest、`planned / started / succeeded / verified`、资源 identity、ownership marker 和失败阶段。
- 未收口的 provision 会冻结 resume、rework 和 abandon；未收口的 promotion 会冻结重测、晋升、返工、放弃和吸收；cleanup 开始后冻结其他写操作。
- Git 或文件系统已改变而 TASK 未更新时，先运行对应 reconcile。不能根据 branch、workspace 或会话名称猜测重做或删除。
- workspace 与 branch 只剩一项、保护 ref 漂移、ownership 不明或已清理会话缺失 thread ID 时保留现场并转人工。

## 清理与真实会话归档

`cleanup` 只清理受控 workspace 和普通临时分支，并验证 delivery 保护 ref。存在真实会话时返回：

```text
ARCHIVE_THREAD_REQUIRED:<thread_id>
```

随后按当前工具能力处理：

1. 有真实会话归档工具时立即调用。成功后运行：

```bash
python3 docs/collaboration/scripts/agent_team_temporary.py session-mark \
  --state archived \
  --evidence "host=<真实工具> thread_id=<真实ID> archived=true"
```

2. 没有归档工具或自动调用失败时，原样提醒：

```text
我目前无法自动归档这个会话。请你手动归档临时外包会话「<会话名称>」（会话 ID：<真实ID>），归档完成后告诉我一声。
```

用户明确表示已完成后，使用 `user_confirmation=<确认指针> thread_id=<真实ID> archived=true` 登记。此前保持 `temporary_session=standby`，不得声称已归档；这条待办不阻断其他安全工作。

创建阶段已放弃且从未产生真实 thread ID 时返回 `NO_THREAD_ARCHIVE_REQUIRED`，会话状态由清理事务收口为 `cancelled`。资源已清理、会话仍 standby 但 thread ID 为空属于损坏真值，必须拒绝。

thread ID 在正式与临时会话间全局唯一，按原始字符串精确绑定；不能包含空白、以 `=` 开头或超过 300 字符。换班、failed 重试、规则重建、amend 和 rework 都不能覆盖已经登记但尚未归档的 ID。

`pending-archives` 是可重复调用的只读查询，不改变 TASK。旧协议中缺少可信收据的 archived 会话退回 standby；能精确绑定 thread ID、`archived=true` 和 host 或 user confirmation 的收据继续保留，不能只因版本旧而抹掉。
