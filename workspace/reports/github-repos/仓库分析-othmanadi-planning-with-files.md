# GitHub 仓库分析：OthmanAdi/planning-with-files

## 1. 项目结论

一个基于文件系统的持久化规划技能（skill），让 AI 编程 Agent 用 `task_plan.md`、`findings.md`、`progress.md` 三个 Markdown 文件代替上下文窗口来追踪任务。核心理念直接来自 Manus（Meta 以 20 亿美元收购的 AI Agent 公司）的 Context Engineering。**高度值得研究**，尤其对 AI Agent Gateway 项目的 skill 机制和任务持久化设计有直接参考价值。

## 2. 基本信息

- 仓库地址：https://github.com/OthmanAdi/planning-with-files
- 主要语言：Shell（sh/ps1）+ Markdown
- License：MIT
- Star / Fork：16K+ 开发者安装量
- 最近更新时间：v3.1.3（2026-06-16）
- 默认分支：master
- 项目类型：AI Agent Skill / Plugin

## 3. 项目解决什么问题

AI 编程 Agent 的三大核心痛点：

1. **上下文丢失**：`/clear` 或上下文窗口满了之后，TodoWrite 工具的状态全部消失，Agent 忘记自己在做什么。
2. **目标漂移**：经过 50+ 次工具调用后，模型逐渐忘记最初设定的目标（"lost in the middle"效应）。
3. **错误重复**：失败没有被记录，Agent 可能重复完全相同的失败操作。

解决思路：**把文件系统当硬盘，上下文窗口当内存**。关键状态持久化到磁盘，每次决策前重新读取。

## 4. 核心功能

1. **三文件模式**：`task_plan.md`（阶段追踪）、`findings.md`（研究发现）、`progress.md`（会话日志）
2. **Hook 自动注入**：通过 Claude Code / Codex / Cursor 等 IDE 的生命周期 Hook（UserPromptSubmit、PreToolUse、PostToolUse、Stop、PreCompact）自动在合适的时机将计划内容注入上下文
3. **会话恢复（v2.2）**：`/clear` 后自动从磁盘恢复之前的计划状态
4. **自主模式与门控模式（v3）**：强模型可选降低注入频率（去掉每次工具调用的 plan 重注入，只在 turn 开始时注入），以及完成门（Stop 时阻塞直到所有阶段完成）
5. **哈希证明（v2.37）**：SHA-256 锁定 `task_plan.md`，防止被外部篡改后注入恶意指令
6. **并行计划隔离（v2.36）**：支持 `.planning/YYYY-MM-DD-slug/` 多任务目录，同一个 repo 里同时进行多个独立计划
7. **Ledger 运行账本（v3）**：结构化的 JSONL 运行记录，代替原始 `progress.md` 尾部注入，KV-cache 友好
8. **60+ Agent 兼容**：通过 Agent Skills 开放标准，支持 Claude Code、Cursor、Codex、Copilot、Gemini CLI、Kiro、Pi、OpenClaw 等

## 5. 技术栈与依赖

- 核心：纯 Shell（POSIX sh）+ PowerShell，零运行时依赖
- IDE 集成：各平台 Hook 机制（Claude Code hooks、Codex hooks.json、Cursor hooks.json、Copilot hooks config 等）
- 安装分发：`npx skills add` / Claude Code Plugin / Git submodule / 直接复制
- 测试：Python pytest（184 条测试，含 SKILL.md frontmatter YAML 校验、版本一致性检查）
- 无模型 API 依赖、无外部服务、无数据库

## 6. 目录结构解读

```text
planning-with-files/
├── skills/planning-with-files/    # 核心 skill（英文）
│   ├── SKILL.md                   # Skill 定义 + Hook 配置 + 使用指南
│   ├── reference.md               # Manus Context Engineering 六原则详解
│   ├── examples.md                # 实际使用案例
│   ├── templates/                 # task_plan / findings / progress 模板
│   └── scripts/                   # init-session, check-complete, attest-plan, gate-stop 等
├── skills/planning-with-files-zh/ # 中文版（简/繁/阿/德/西）
├── docs/                          # 17+ 平台各自的安装指南
├── commands/                      # /plan-goal, /plan-loop 等 slash 命令
├── scripts/                       # 同步脚本、版本一致性检查
├── .codebuddy/ .codex/ .cursor/   # 各 IDE 的适配层（hooks + SKILL.md 镜像）
│   .factory/ .continue/ .pi/
│   .gemini/ .kiro/ .opencode/
│   .github/ .hermes/ .mastracode/
└── CHANGELOG.md                   # 1397 行详细变更记录
```

关键设计点：每个 IDE 适配目录里都有 SKILL.md 的**镜像副本**，保持 hook 配置与对应平台的路径约定一致。版本一致性由测试套件自动检查。

## 7. 核心流程或架构理解

### Agent 执行流程

```text
1. UserPromptSubmit hook 触发
   → inject-plan.sh 读取 task_plan.md + 进度摘要 → 注入上下文顶部

2. PreToolUse hook 触发（每次 Write/Edit/Bash/Read 之前）
   → 传统模式：再次注入 plan head（防止漂移）
   → autonomous 模式：跳过（减少 token 开销）

3. 模型决策并执行工具调用

4. PostToolUse hook 触发（每次 Write/Edit 之后）
   → 提醒模型更新 progress.md

5. Stop hook 触发
   → check-complete.sh 检查所有阶段是否完成
   → gated 模式：未完成则阻塞停止（最多 20 次，有 stall 检测防止死循环）
```

### 上下文注入机制

- 使用静态分隔符包裹注入内容（v3: 带 nonce 的动态分隔符）
- 注入内容被标记为结构化数据，指导模型不执行其中的指令
- v3 模式：只注入结构化的 ledger 摘要而非原始 `tail -20 progress.md`，保证 KV-cache 稳定性

### 证明锁定（Attestation）

```text
attest-plan.sh → SHA-256(task_plan.md) → .planning/<id>/.attestation
每次 hook 触发时 → 重算哈希 → 与存储值对比 → 不匹配则拒绝注入
```

## 8. 如何本地运行

作为 skill 安装到 Claude Code：

```bash
npx skills add OthmanAdi/planning-with-files --skill planning-with-files -g
```

或在 Claude Code 中：
```
/plugin marketplace add OthmanAdi/planning-with-files
/plugin install planning-with-files@planning-with-files
```

然后开始一个规划任务：
```bash
sh scripts/init-session.sh "My Complex Task"
```

**但注意**：这个项目本身不是一个可运行的独立应用——它是一组 hook 脚本 + SKILL.md 定义，依赖于宿主 IDE（如 Claude Code）的生命周期事件来触发。

## 9. 值得重点阅读的文件

| 文件 | 理由 |
|------|------|
| `skills/planning-with-files/SKILL.md` | 完整的 skill 定义、hook 配置、核心规则，是整个项目的"宪法" |
| `skills/planning-with-files/reference.md` | Manus Context Engineering 六原则详解，理论根基 |
| `skills/planning-with-files/scripts/init-session.sh` | 会话初始化，含 slug 模式、v3 mode marker、nonce 生成、自动证明 |
| `skills/planning-with-files/scripts/check-complete.sh` | 完成检查 + 门控决策表，v3 gate 的核心实现 |
| `skills/planning-with-files/scripts/gate-stop.sh` | Stop hook 分发器，决定是否阻塞 Agent 停止 |
| `skills/planning-with-files/scripts/inject-plan.sh` | 计划注入逻辑，含证明校验和 v3 模式路径 |
| `skills/planning-with-files/scripts/attest-plan.sh` | SHA-256 计划锁定，安全防线 2 |
| `skills/planning-with-files/scripts/session-catchup.py` | 会话恢复，读 IDE session store 重建状态 |
| `docs/evals.md` | 96.7% 通过率的评估方法、数据集和断言列表 |
| `CHANGELOG.md` | 1397 行变更记录，展示一个成熟开源项目的迭代节奏 |

## 10. 可借鉴点（结合 AI Agent Gateway）

| Gateway 方向 | 可借鉴内容 |
|-------------|-----------|
| **Skill 机制** | `SKILL.md` 的 frontmatter（hooks, allowed-tools, user-invocable）+ markdown body 模式可以直接参考。Gateway 的 skill 加载与发现可以沿用此格式 |
| **工具调用 Hook** | 在 Tool Call 前后注入上下文的设计——Gateway 的 Agent Loop 可以加入 PreToolUse / PostToolUse 阶段 |
| **Gated Completion** | v3 的完成门控：不是等到 Agent 自己说"做完了"，而是**对比磁盘上的 task_plan.md 的事实状态**。Gateway 可以借鉴——任务投递完成后检查目标产物而非仅看 Agent 回复 |
| **Persistent Plan** | `task_plan.md` 作为跨会话持久化执行状态的模式——Gateway 的 session 管理可以引入类似机制，不只是记住对话，而是记住"当前任务进度" |
| **多 Agent 共享状态** | 通过文件系统做 Agent 间通信（`.planning/<id>/ledger-<agent>.jsonl`）——适合 Gateway 的多 Agent 协作场景 |
| **上下文注入 vs 上下文窗口管理** | `inject-plan.sh` 的模式——在每轮开始时向模型上下文注入关键信息。Gateway 可以在 context assembly 时做类似的事情 |
| **Attestation / 安全** | 计划哈希锁定 + 注入拒绝——防止被外部篡改的 plan 内容进入模型上下文，Gateway 的多租户场景可以借鉴 |
| **版本一致性自动化** | `scripts/sync-ide-folders.py` + `tests/test_skill_frontmatter_valid.py`——17 个 IDE 适配副本的自动同步和校验，Gateway 的多后端适配可以参考 |
| **Degradation 设计** | 门控机制的 host capability tiers（硬阻塞 → 追随注入 → 仅通知）——根据运行平台能力优雅降级，Gateway 的通道适配可以借鉴 |

## 11. 风险与不足

| 风险 | 说明 |
|------|------|
| **依赖宿主 IDE** | 不是一个独立应用，功能严重依赖 Claude Code / Codex 等的 hook 机制。Gateway 如果要集成，需要自己实现等价的 hook 触发点 |
| **安全模型的诚实局限** | 文档明确承认 nonce 分隔符不能防御已拥有 plan 写入权限的攻击者，prompt injection 的防御主要靠 attestation。对于 Gateway 的多租户场景，这个假设需要重新评估 |
| **复杂度过高** | 1397 行 CHANGELOG、17 个 IDE 适配目录、大量 shell 脚本——维护成本高，IDE 适配层的版本同步是已知的重复问题 |
| **评估数据时效性** | 96.7% 通过率基于 2026-03-06 的 sonnet-4-6，不覆盖 v3 autonomous/gated 模式和新模型 |
| **无国际化测试覆盖** | 虽然有 6 种语言的 SKILL.md，但测试主要覆盖英文版 |
| **文档维护滞后风险** | 每个版本需要同步更新 17 个 IDE 的文档和适配层 |

## 12. 成熟度评分

| 维度 | 评分 | 说明 |
|---|---|---|
| 文档完整度 | 5/5 | 每个平台有独立安装指南，quickstart/workflow/troubleshooting/evals 齐全 |
| 代码结构清晰度 | 4/5 | 核心逻辑清晰，但 IDE 适配层的镜像副本管理复杂 |
| 可运行性 | 4/5 | 在 Claude Code 上开箱即用，其他平台需要按指南配置 |
| 维护活跃度 | 5/5 | 2026-06-16 最新版本，频繁发布，社区活跃 |
| 与当前 Gateway 项目的相关度 | 5/5 | 直接关系到 Gateway 的 skill 机制、Agent loop、任务持久化、安全注入 |

## 13. 最终建议

**强烈建议深度研究**，不适合直接使用（它不是独立应用），但设计思路高度可借鉴。

最值得借鉴的三个点：

1. **SKILL.md 格式 + Hook 生命周期模型**：这是 Gateway 设计 skill 机制的直接参考模板。`name/description/hooks/allowed-tools/user-invocable` 的 frontmatter 结构很成熟。

2. **v3 的 Gated Completion 模式**：用磁盘上的 task_plan.md 作为完成判定的"真相来源"，而不是信任 Agent 自述的完成状态。这个"外部 ground truth" 的思路对 Gateway 的可靠投递机制有启发。

3. **安全注入框架**：两层防线（分隔符 + SHA 证明）、v3 的 nonce 动态分隔符、attestation 默认开启、结构化 ledger 注入代替自由文本注入——这些都可以直接融入 Gateway 的上下文组装和 prompt injection 防御设计。

下一步建议：精读 `SKILL.md` 和 `reference.md`，然后读 `check-complete.sh`（门控决策表）和 `inject-plan.sh`（上下文注入逻辑），这三份材料加起来不到 2000 行，能覆盖核心设计思想。
