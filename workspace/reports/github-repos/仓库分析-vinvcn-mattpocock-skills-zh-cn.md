# GitHub 仓库分析：vinvcn/mattpocock-skills-zh-CN

## 1. 项目结论

**Matt Pocock Agent Skills 的简体中文本地化版本**——一套面向真实工程的 AI Agent 技能集合，解决 Agent coding 中的四大失败模式（需求偏差、表达啰嗦、代码跑不通、代码腐化），由 OpenAI Codex 执行翻译，按上游内容刷新同步。对 Gateway 项目，在「Skill 设计哲学」「上下文精简策略」「Agent 工作流编排」三方面有直接参考价值。

---

## 2. 基本信息

- 仓库地址：https://github.com/vinvcn/mattpocock-skills-zh-CN
- 主要语言：Markdown / Shell（纯文档仓库，无可执行代码）
- License：MIT
- Star / Fork：约 1.4k / 118
- 最近更新时间：2026-06-16
- 默认分支：main
- 项目类型：Agent Skills 集合 / 本地化翻译项目
- 上游仓库：https://github.com/mattpocock/skills（135k+ stars）

---

## 3. 项目解决什么问题

Matt Pocock（TypeScript 教育家，Total TypeScript 创始人）在日常使用 Claude Code、Codex 等 Agent 时反复遇到四类失败模式：

1. **Agent 不理解用户真正要什么**（需求偏差）→ /grill-me、/grill-with-docs 做需求对齐
2. **Agent 表达太啰嗦**（沟通效率低）→ /caveman 精简输出，CONTEXT.md 建立共享术语
3. **代码跑不起来**（缺乏反馈循环）→ /tdd red-green-refactor，/diagnose 调试循环
4. **代码腐化成 Ball of Mud**（架构持续恶化）→ /to-prd 规划、/zoom-out 俯瞰、/improve-codebase-architecture 重构

本中文版的核心价值：**让中文母语用户用母语理解和配置这些 skills**，减少中英混杂带来的概念转换成本和 prompt 歧义。

---

## 4. 核心功能

### 4.1 Skill 分类体系（6 个 bucket）

| Bucket | 用途 | 状态 |
|--------|------|------|
| engineering/ | 日常代码工作 | 主流使用 |
| productivity/ | 通用非代码工作流 | 主流使用 |
| misc/ | 保留但很少使用 | 可用 |
| in-progress/ | 开发中，暂不推广 | 预览 |
| personal/ | 绑定作者的本地设置 | 不推广 |
| deprecated/ | 不再使用 | 废弃 |

### 4.2 关键 Engineering Skills

| Skill | 类型 | 解决的问题 |
|-------|------|-----------|
| /grill-me | 用户调用 | 在编码前让 Agent 提出详细问题，对齐需求 |
| /grill-with-docs | 用户调用 | 同上 + 建立 CONTEXT.md 共享术语 + 写入 ADR |
| /setup-matt-pocock-skills | 用户调用 | 一次性配置 issue tracker、labels、docs 目录 |
| /to-prd | 用户调用 | 将需求转化为结构化 PRD |
| /to-issues | 用户调用 | 将 PRD 拆解为可追踪的 issues |
| /triage | 用户调用 | Issue 状态机分类（needs-triage → ready-for-agent） |
| /tdd | 用户调用 | Red-green-refactor 测试驱动开发 |
| /diagnose | 用户调用 | 系统化调试循环 |
| /zoom-out | 用户调用 | 俯瞰当前架构状态 |
| /improve-codebase-architecture | 用户调用 | 拯救已成 Ball of Mud 的代码库 |
| /codebase-design | 模型调用 | 代码设计指导（v1.0 新增） |
| /domain-modeling | 模型调用 | 领域建模（v1.0 新增） |
| /handoff | 用户调用 | Agent 间工作交接 |
| /review | 用户调用 | 代码审查 |
| /prototype | 用户调用 | 快速原型 |
| /teach | 用户调用 | 教学/解释代码 |
| /ask-matt | 用户调用 | 路由 skill，了解所有 skill 如何协同 |
| /caveman | 用户调用 | 极致精简 Agent 输出 |

### 4.3 翻译策略

- **skill-guided content localization**：用 .skills/translate-skill/SKILL.md 定义翻译规则
- 只翻译自然语言说明，保留：目录名、skill name、frontmatter key、命令、代码块、路径、URL、package/tool/API identifiers
- 由 OpenAI Codex（GPT-5 coding agent）执行翻译
- 按上游内容刷新同步，不同步 Git 历史
- 安装命令指向 vinvcn/mattpocock-skills-zh-CN（而非上游）

---

## 5. 技术栈与依赖

| 层面 | 技术 |
|------|------|
| 内容格式 | Markdown（SKILL.md） |
| 分发 | npx skills add / skills.sh 网页 |
| 翻译工具 | OpenAI Codex (GPT-5 coding agent) |
| 验证 | Node.js 脚本（check-translation.mjs、audit-english.mjs） |
| 版本控制 | Git |
| 依赖管理 | 零运行时依赖（纯文档仓库） |

---

## 6. 目录结构

    mattpocock-skills-zh-CN/
    ├── .claude-plugin/          # Claude Code 插件配置
    │   └── plugin.json          # 已发布 skill 注册表
    ├── .skills/
    │   └── translate-skill/     # 🔥 翻译流程的元 Skill
    │       └── SKILL.md         # 翻译规则和刷新流程
    ├── docs/adr/                # 架构决策记录
    ├── scripts/
    │   ├── check-translation.mjs
    │   └── audit-english.mjs
    ├── skills/
    │   ├── engineering/         # ⭐ 日常代码工作
    │   ├── productivity/        # 非代码工作流
    │   ├── misc/                # 保留但少用
    │   ├── in-progress/         # 开发中
    │   ├── personal/            # 作者个人
    │   └── deprecated/          # 废弃
    ├── AGENTS.md               # 🔥 仓库治理规则
    ├── CLAUDE.md               # Claude Code 入口规则
    ├── CONTEXT.md              # 🔥 共享术语定义
    └── README.md

---

## 7. 核心架构理解

### 7.1 Progressive Disclosure（v1.0，token 节省 63%）

- Skill 的 SKILL.md 不再全文塞进 system prompt
- 只加载 skill name + 一句话描述
- Agent 判断需要时再读取完整 SKILL.md

> 直接解决 Gateway 把 9 个 Skill 全文塞进 system prompt（~8K-12K token）的问题。

### 7.2 用户调用 vs 模型调用二分法

- **用户调用**：如 /grill-me、/tdd，用户主动触发
- **模型调用**：如 /codebase-design，Agent 在合适时机自动调用

### 7.3 Shared Language 模式

CONTEXT.md 定义项目共享术语（类似 DDD Ubiquitous Language），每个术语有标准名称、定义、避免的别名。Agent 用统一术语沟通，减少啰嗦。

---

## 8. 如何本地运行

    npx skills add vinvcn/mattpocock-skills-zh-CN
    # 在 Agent 中运行
    /setup-matt-pocock-skills

纯 Markdown，零运行时依赖。

---

## 9. 值得重点阅读的文件

| 文件 | 阅读理由 |
|------|----------|
| README.md | Skill 设计哲学（四大失败模式 + 解决方案） |
| AGENTS.md | 仓库治理规则——bucket 组织、翻译刷新流程 |
| CONTEXT.md | Shared Language 术语定义 |
| .skills/translate-skill/SKILL.md | **元 Skill**——用 Skill 定义翻译 Skill 的递归设计 |
| .claude-plugin/plugin.json | Skill 注册表 |
| skills/engineering/grill-with-docs/SKILL.md | 最复杂 Skill——需求对齐 + 术语建立 + ADR |
| skills/engineering/tdd/SKILL.md | Red-green-refactor 的 Agent 化实现 |
| scripts/check-translation.mjs | 翻译完整性检查 |

---

## 10. 对 Gateway 的可借鉴点

| 借鉴方向 | 说明 | 优先级 |
|----------|------|--------|
| **Progressive Disclosure** | Skill 按需加载，token 节省 63% | P0 |
| **Skill 分类体系** | engineering/productivity/misc 分桶 + 治理规则 | P0 |
| **CONTEXT.md 共享语言** | 项目专属术语，减少 Agent 啰嗦 | P1 |
| **用户调用 vs 模型调用** | Agent 可自动触发 Skill | P1 |
| **元 Skill 模式** | 用 Skill 管理 Skill 的递归设计 | P2 |
| **Setup Skill 模式** | /setup-gateway 一次性引导配置 | P2 |

---

## 11. 风险与不足

| 风险点 | 说明 |
|--------|------|
| 对上游强依赖 | 本质是翻译镜像，独立价值有限 |
| 翻译质量依赖 Agent | Codex 翻译可能引入偏差 |
| 无CI自动化翻译 | 刷新由维护者手动触发 |
| 无行为测试 | 仅格式检查，无法验证真实 Agent 行为 |
| 社区参与低 | 1 issue, 0 PR |

---

## 12. 成熟度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 文档完整度 | 5/5 | 工程哲学完整，治理清晰，格式统一 |
| 内容结构清晰度 | 5/5 | 6 bucket 分类清晰，元 Skill + 检查脚本自成体系 |
| 可运行性 | 5/5 | 一键安装，零依赖 |
| 维护活跃度 | 3/5 | 跟随上游活跃，社区参与低 |
| 与 Gateway 相关度 | 5/5 | Progressive Disclosure、Skill 分类、Shared Language 直接可用 |

---

## 13. 最终建议

**强烈推荐研究，重点关注三项：**

1. **Progressive Disclosure 机制** → 解决 Gateway Skill 全文塞 prompt 的 token 浪费
2. **Skill 分类体系 + 治理规则** → 改造 Gateway 的 skills/ 目录组织
3. **CONTEXT.md 共享语言** → 在 Gateway 工作区实现项目专属术语

**不建议直接安装**——它是 TypeScript 前端场景，与 Gateway 的 Python 后端不匹配。

**下一步：**
1. 为 Gateway 创建 CONTEXT.md
2. 设计 Progressive Disclosure 加载策略
3. 参考 .skills/translate-skill/SKILL.md 做「Skill 创建向导」
