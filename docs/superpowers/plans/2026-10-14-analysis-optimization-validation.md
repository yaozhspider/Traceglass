# 优化实现与验收记录（2026-10-14）

代码和 Skill 接入已完成，本地回归通过。真实静态模型评测仍超时；当前没有准确率、
成本下降或动态可利用性的验收结论。以下区分实现验证与后续质量验证。

## 实现范围与证据

| 方案部分 | 已实现内容 | 主要验证 |
| --- | --- | --- |
| Skill 接入 | 从 `~/.cursor/skills/unpacking` 与 `~/.cursor/skills/vuln-discovery` 打包完整离线引用、来源哈希和 Traceglass 沙箱适配；新增 vulnerability-hunting | native Skill 同步、保留用户编辑；当前 wheel 内 16 个 Skill，新包及引用逐字节核对 |
| B1–B4 漏洞契约 | 共享 verdict/class/exploitability；区分静态缺陷、可达性、可控性、动态利用；兼容旧状态 | Review 正反例、过度利用声明、hardened 严格度、7 个新增 replay 用例 |
| A3 委派 | 默认单 Agent；Create/Invite/后续 TeamSay 绑定原生 task_id、依赖和 owner；保留 8 人硬上限 | 并发认领/失败回滚；真实 default/bypass 产品链路；原生 TaskUpdate 拒绝后不改变 owner |
| A4 工具入口 | SearchAnalysisTools 统一搜索/激活/备用后端；旧 REA 名称兼容 | 真实 MCP 搜索→reset_tools→执行；缺缓存显式发现可在同轮生效；权限拒绝零启动 |
| A5 证据账本 | 普通/结构化结果、错误/超时/取消，分离附件，原生 metadata 回执；大结果以短回执进入模型 | 1 MiB 截断与共享原文；租户/样本/哈希/定位/失败/重启；附件下载跨账号拒绝；产品原始历史保留 |
| A5 严格引用 | 新会话持久化 strict 模式；旧状态与 replay 兼容；原生文件哈希及显式 IDA handle 绑定 | 文本引用不能在重启后关闭 strict；Read/reverse/IDA 关联；不猜全局当前样本 |
| A3 REA 生命周期 | kit/schema 绑定的延迟 MCP 代理，默认 2 个连接、600 秒空闲回收、显式释放 | 并发首次连接、取消 readiness/连接释放、配额、活动调用保护、独立二进制状态、代次/句柄失效和路径工具重连 |
| A6 verifier | 新原生 Agent 上下文，仅传范围、拟议结论和授权归档原文 | 真实 AgentCreate 的模型输入检查；不携带 leader 推理、描述或历史 |
| A7 迁移 | 版本化内置 core/policy；仅升级已知默认；自定义角色及后缀保留 | 本地/账号迁移、幂等、旧 worker 指引；消除旧 Docker Bash 动态执行冲突 |
| A1 评测 | 4 个合成 C 静态正反例、Linux x86_64 object、独立 manifest/rubric；独立与产品模式分开 | 样本哈希、答案隔离、严格 Review、最终结论绑定、最终 answer 解析、原生团队/worker、静态 allowlist、超时归类 |
| A1 成本与复现 | 原生 API usage/尝试、工具错误/重复、REA 启动/占用、prompt/Skill 引用/schema/代码指纹 | 上下文替换后仍保留 usage；独立复核验证原生 compress_context 的直接模型调用计费；比较参数不一致显式提示 |

`review_passed` 只表示结构通过，引用校验只验证可访问来源、完整性、样本和定位。
这些检查不证明数据流或分析结论正确。动态执行继续要求授权注册的 sandbox MCP。

## 最终本地验证

- `.venv/bin/python -m pytest -o addopts='' -q`：**238 passed，93 条 AgentScope 弃用警告，42.46 秒**。
- 前端 `npm test`：**51 passed**；`npm run build`：TypeScript 与 Vite 构建通过。
- `git diff --check` 与 Python compileall：通过。
- wheel 构建与资源检查：通过；上一阶段为 15 个 Skill，本次加入 vuln-discovery 后为 16 个；
  新 Skill/引用及来源清单与源码逐字节一致，
  不含 `__pycache__`/`.pyc`。排除规则也避免本地编译缓存进入发布包。
- 独立代码复核发现并修正了最终 verdict 未绑定 Review、错误回用旧评估、长结果挤掉回执、
  压缩丢失费用统计，以及原生聚合 assistant 消息中最终 answer 解析的问题；均有回归。

仓库原有账号、UI、REA 等未提交修改已保留；没有提交、发布、重启用户服务或执行目标。

### 2026-10-15：vuln-discovery 接入补验

- 导入完整 9 个文件（SKILL.md 与 8 份离线参考），保留原目录；来源和部署文件分别记录 SHA-256。
- 增加漏洞挖掘路由和评测 Skill 指纹；新建/已有工作区的 native 同步、Skill Viewer、资源完整性与用户编辑保留均通过。
- 完整 Python 回归：242 passed，93 条既有 AgentScope 弃用警告，50.07 秒。
- 干净构建 wheel：16 个 Skill、87 个资源逐字节一致，无字节码；来源哈希、打包哈希与本地引用有效。
- 后端已按用户要求重启，保留账号数据与原环境；8002 后端、5177 前端及前端 API 代理均返回 200。
- 未修改前端，未执行目标或验证真实漏洞挖掘效果。

## 真实环境观察与限制

模型为当前配置的 `glm-5.3-flash`；REA 使用部署的不可变 6.3.0-pwntools kit。
四个对象文件只编译、未链接执行；独立答案和源码不进入 Agent 工作区。

| 运行 | 结果 |
| --- | --- |
| replay `stack-overflow-reachable`，90 秒预算 | 66.55 秒完成；结构 rubric 通过；17,276 tokens；零 REA 启动。需要人工检查语义对应 |
| 静态 `copy-unchecked`，120 秒预算 | 121.38 秒超时；到达真实 REA 反编译/指令读取；无最终评分 |
| 最终静态复测，180 秒预算 | 181.28 秒超时；16 次工具执行，15 成功/1 错误；1 次 REA 启动、180.93 连接秒；226,973 观测 tokens；无最终评分 |

最终运行目录：`traceglass-data/eval-live-static-20261014-final/`。
原始轨迹、独立 rubric、对象哈希及成本记录保留在忽略的本地输出中。
两次静态尝试跨越实现与预算变更，不是受控 A/B 比较。

真实产品团队链路已用脚本化模型验证，尚未验证真实模型的产品委派质量。
当前没有授权动态沙箱部署，因此没有动态执行或可利用性复现结论。
配额与委派锁均为单进程机制；多 API 进程部署需共享配额/锁。
暂停和等待批准继续计算 REA 空闲时间；运行时明确提示重新打开样本，设 idle=0 可关闭回收。

后续质量验收仍需：成功完成的真实静态评估、同参数重复正反例/留出样本对比与独立人工复核；
有授权动态环境时再按 sandbox 流程验证工件和利用声明。当前不宣称分析准确率或成本已改善。
