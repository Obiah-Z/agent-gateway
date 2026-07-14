# Agent 接入简化机制调研报告

## 背景

当前新增一个 Agent 需要同步修改多处内容。以 `internship-assistant-zhanghaibo` 为例，除了新增 Agent 配置和 prompt，还需要修改入口提示词、个人秘书提示词、协作运行时别名和关键词、能力目录分类、路由契约、路由评估样例和多组测试。这个流程可以工作，但接入成本高，且每新增一个垂直 Agent 都容易漏改某一层。

本文目标是调研当前耦合点，并提出一种更低成本、更不容易漏改的 Agent 接入机制。

## 实施状态

截至当前实现，报告中的 Phase 1 和 Phase 2 已落地为兼容式改造：

- 已新增 `agent_gateway/runtime/domain/agent_manifest.py`，支持读取 `workspace/agents/*/agent.yaml`，并将 manifest 转换为现有 `AgentConfig`。
- 已新增 `scripts/check_agent_manifests.py`，用于校验 manifest、工具白名单、别名冲突和写入类契约确认要求。
- 已新增 `scripts/scaffold_agent.py`，用于生成新 Agent 的 manifest 和 prompt 骨架。
- 已为 `diet-assistant-zhanghaibo` 和 `internship-assistant-zhanghaibo` 补充 `agent.yaml` 作为试点。
- `load_agents()` 已支持用 manifest 覆盖同 id 的 `config/agents.json` 配置，并保留旧配置兼容。
- `classify_task_intent` 已支持从 manifest routing catalog 派生专科 Agent 分类项。
- `list_agent_capabilities` 已支持 manifest-only Agent，不再必须先写入 `config/agents.json`。
- `CollaborationRuntime` 的专科 Agent 别名、关键词兜底和专家历史持久化策略已优先读取 manifest。
- `AgentRoutingContract` 已支持聚合 manifest 中的 contract examples，减少新增专科 Agent 时手写 Python 常量的必要性。

暂未实施 Phase 3 的动态 tool bundle。原因是该阶段涉及工具 store 生命周期、持久化后端注入和启动失败策略，风险明显高于 Agent 元数据接入。当前建议先使用 Phase 1/2 简化“使用已有工具的 Agent”接入；需要新增工具时仍按现有方式注册，待 manifest 路由稳定后再推进 tool bundle。

当前建议的验收命令：

```bash
python scripts/check_agent_manifests.py
pytest tests/test_agent_manifest_loader.py tests/test_builtin_tools.py::test_agent_manifest_only_agent_appears_in_capability_catalog_and_classifier tests/test_collaboration_runtime.py tests/test_agent_contracts.py tests/test_agent_routing_eval.py -q
```

## 当前接入链路

新增 Agent 当前至少涉及以下位置：

| 类型 | 当前文件 | 作用 | 问题 |
| --- | --- | --- | --- |
| Agent 基础配置 | `config/agents.json` | Agent id、名称、模型、dm_scope、工具白名单、记忆策略、prompt 目录 | 必须手写完整 JSON，工具列表容易漏 |
| Prompt | `workspace/agents/<agent-id>/IDENTITY.md`、`SOUL.md`、`TOOLS.md` | 定义身份、职责、工具使用规则 | 与配置分离，路由元数据无法从 prompt 稳定读取 |
| 入口/上游 Agent prompt | `workspace/agents/wework-entry/*`、`workspace/agents/personal-secretary-zhanghaibo/*` | 告诉入口或秘书遇到哪些意图要交给新 Agent | 每新增 Agent 都要改上游 prompt |
| 协作运行时 | `agent_gateway/runtime/execution/collaboration.py` | `_normalize_delegate_target()` 中维护别名、blocked Agent 改写和关键词兜底路由 | 专科 Agent 被硬编码到运行时 |
| 能力目录/分类 | `agent_gateway/ai/tools/builtin.py` | `classify_task_intent` 关键词表、`list_agent_capabilities` layer 分类、契约搜索关键词 | 新 Agent 的关键词、层级、解释都要写进代码 |
| 路由契约 | `agent_gateway/ai/agent_contracts.py` | `DEFAULT_AGENT_ROUTING_CONTRACTS` 声明期望 intent、Agent、关键工具和风险属性 | 契约与 Agent 配置分离，容易不一致 |
| 路由评估 | `scripts/eval_agent_routing.py`、`tests/test_agent_routing_eval.py` | 验证入口判断和工具白名单 | 样例数量、期望 Agent、风险契约需要手动更新 |
| 专项测试 | `tests/test_<domain>_agent_config.py`、`tests/test_collaboration_runtime.py` | 验证 prompt、绑定、协作别名和 session key | 每个 Agent 容易复制一组类似测试 |

当前系统已经有一部分动态能力：`list_agent_capabilities` 会读取 `config/agents.json` 和对应 `IDENTITY.md` 生成能力目录；`GatewayControlPlane.reload_agents()` 会从配置或状态仓储重载 Agent。但分类、路由、契约和上游提示仍主要依赖静态代码和手写 prompt。

## 核心问题

1. **Agent 元数据分散**：一个 Agent 的职责、别名、关键词、风险属性、所需工具、路由样例分散在 JSON、Markdown、Python 和测试中。
2. **运行时硬编码专科 Agent**：协作运行时需要知道 `diet-assistant-zhanghaibo`、`internship-assistant-zhanghaibo` 等具体 id，新增 Agent 就要改代码。
3. **入口提示词不可自动同步**：入口 Agent 和个人秘书 prompt 需要人工写“遇到 X 交给 Y”，容易和真实配置不一致。
4. **工具注册仍是代码级接入**：如果 Agent 带新工具，还要改 `app.py` 注册 store/tools。这里属于“工具能力接入”，比 Agent 配置更底层，短期不能完全消除，但可以把“Agent 使用已有工具”的接入简化。
5. **测试按 Agent 手写**：路由契约和配置测试没有完全从统一 manifest 生成，导致新增 Agent 时测试改动量大。

## 推荐方案：Agent Manifest 注册机制

推荐引入一个“单 Agent 单 manifest”的接入方式。每个 Agent 在自己的目录维护一个机器可读文件，例如：

```text
workspace/agents/<agent-id>/
  agent.yaml
  IDENTITY.md
  SOUL.md
  TOOLS.md
  CRON.json        # 可选
```

`agent.yaml` 作为唯一权威源，负责描述运行时和路由需要的结构化信息：

```yaml
id: internship-assistant-zhanghaibo
name: 张海波实习记录助手
layer: personal
dm_scope: per-account-channel-peer
owner_scope: user:wework:wework-main:direct:zhanghaibo
personality: 事实优先、结构清晰、面向复盘和日报沉淀

prompt:
  use_global_files: true
  skills_enabled: true

tools:
  mode: allowlist
  names:
    - get_current_time
    - internship_log_add
    - format_internship_log_entry
    - internship_log_list
    - internship_log_search
    - format_internship_log_list
    - internship_daily_report_generate
    - format_internship_daily_report
    - memory_search
    - format_memory_search

memory:
  enabled: true
  auto_recall: true
  top_k: 4

routing:
  intent: internship
  aliases:
    - internship-agent
    - internship
    - 实习助手
    - 实习记录助手
  keywords:
    - 实习
    - 日报
    - 周报
    - 导师
    - mentor
    - 项目进展
    - 联调
    - blocker
    - 简历素材
  reason: 用户关注实习过程记录、日报周报、导师反馈或项目卡点，实习记录助手更适合处理。
  next: 确认用户身份、日期和要记录的事实后，建议交给 internship-assistant-zhanghaibo。
  persist_delegate_history: true
  blocked_controller: false

contract:
  examples:
    - name: internship
      user_text: 今天实习做了接口联调，遇到一个 blocker，帮我记录一下并生成日报
      required_tools:
        - internship_log_add
        - format_internship_log_entry
        - internship_daily_report_generate
      read_only: false
      requires_confirmation: true
      requires_collaboration: false
```

### 派生内容

引入 manifest 后，以下内容可以自动派生：

| 派生项 | 从哪里派生 | 替代当前手写位置 |
| --- | --- | --- |
| `AgentConfig` | `agent.yaml` + prompt 目录 | `config/agents.json` 中重复配置 |
| 能力目录 layer/职责 | `agent.yaml.layer` + `IDENTITY.md` | `builtin.py:list_agent_capabilities()` 中的前缀判断 |
| 入口意图分类 | `routing.intent/keywords/reason/next` | `builtin.py:classify_task_intent` 的硬编码列表 |
| 协作别名归一 | `routing.aliases` | `collaboration.py:_normalize_delegate_target()` 的 aliases 字典 |
| 专科 Agent 历史持久化 | `routing.persist_delegate_history` | `collaboration.py:_should_persist_delegate_history()` 的硬编码集合 |
| 路由契约 | `contract.examples` | `agent_contracts.py:DEFAULT_AGENT_ROUTING_CONTRACTS` 中个人/专科样例 |
| 上游 prompt 目录说明 | 所有 manifest 汇总 | `wework-entry`、`personal-secretary` prompt 中手写“遇到 X 交给 Y” |
| 配置检查 | manifest schema + 工具 allowlist 校验 | 多个手写 config 测试的一部分 |

## 目标架构

```text
workspace/agents/*/agent.yaml
        │
        ▼
AgentManifestLoader
        │
        ├── build AgentConfig 列表
        ├── build routing catalog
        ├── build capability catalog
        ├── build AgentRoutingContract 列表
        └── build prompt injection block
```

运行时只依赖统一 catalog：

```text
config_loader / control_plane
  -> 从 manifest 加载 AgentConfig

builtin.classify_task_intent
  -> 从 routing catalog 做关键词/语义匹配

collaboration._normalize_delegate_target
  -> 从 routing catalog 做 alias 和 fallback route

list_agent_capabilities
  -> 从 manifest + prompt 读取能力目录

agent_contracts
  -> 从 manifest contract examples 加载默认契约
```

## 备选方案对比

| 方案 | 做法 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- | --- |
| A. 继续手写多处配置 | 保持现状 | 无迁移成本 | 新增 Agent 仍需改多处，漏改概率高 | 不推荐 |
| B. 只增加生成脚本 | 写 `scripts/scaffold_agent.py` 自动改 JSON、prompt、测试模板 | 快速降低重复劳动 | 仍然生成多处重复源，后续漂移问题仍在 | 可作为过渡 |
| C. 单 manifest + 运行时动态读取 | 每个 Agent 自带 `agent.yaml`，运行时从 manifest 派生配置和路由 | 单一权威源，新增 Agent 改动最少 | 需要改 loader、路由、契约测试 | 推荐 |
| D. 完全语义路由，无关键词/契约 | 只靠 LLM 或 embedding 从 prompt 判断 Agent | 维护成本低 | 可解释性和验收变弱，写入类 Agent 风险高 | 不适合当前系统 |

推荐采用 **C + B 过渡**：先加 manifest loader 和校验脚本，再提供 scaffold 命令生成目录骨架。不要直接跳到纯语义路由。当前代码已完成该推荐路径的第一步：manifest loader、校验脚本、脚手架和 routing catalog 驱动已落地。

## 分阶段落地计划

### Phase 1：引入 manifest，但保持向后兼容

状态：已完成。

新增：

- `agent_gateway/runtime/domain/agent_manifest.py`
- `workspace/agents/*/agent.yaml`
- `scripts/scaffold_agent.py`
- `scripts/check_agent_manifests.py`

加载策略：

1. 优先读取 `workspace/agents/*/agent.yaml`。
2. 继续支持 `config/agents.json`，二者合并时以 manifest 为准或显式报重复 id。
3. `config/agents.json` 可先保留，避免一次性迁移风险。

验收：

- 现有 Agent 能从 manifest 生成等价 `AgentConfig`。
- `agent-gateway doctor` 增加 manifest schema 检查。
- 新 Agent 若只使用已有工具，理论上只需要新增 `workspace/agents/<id>/agent.yaml` 和 prompt 文件。

### Phase 2：路由和能力目录改为 catalog 驱动

状态：已完成主要闭环。

改造：

- `classify_task_intent` 读取 manifest routing catalog。
- `_normalize_delegate_target()` 使用 alias catalog，不再写专科 Agent id。
- `_should_persist_delegate_history()` 使用 `persist_delegate_history`。
- `list_agent_capabilities()` 使用 `layer` 字段，不再通过 id 前缀判断。
- 入口 prompt 增加一个自动注入的“可委托 Agent 摘要”区块，减少手写同步。

保留：

- repo-adoption、research-option-validation 这类复杂协作路线仍可保留专门规则，因为它们不是单 Agent 专科路由，而是跨 Agent workflow。

验收：

- 新增一个只读 demo Agent，只改 manifest/prompt 即可出现在能力目录，并能被别名和关键词路由命中。
- `tests/test_agent_routing_eval.py` 从 manifest contract examples 自动构造默认样例。

### Phase 3：工具包/Agent 包化

状态：暂缓。

对于带新工具的 Agent，引入 tool bundle：

```yaml
tool_bundles:
  - agent_gateway.ai.context.internship:register_internship_tools
stores:
  - agent_gateway.ai.context.internship:InternshipStore
```

应用启动时由 `ToolBundleLoader` 自动实例化 store 并注册工具，减少修改 `app.py` 的次数。

注意：这一步风险更高，因为涉及依赖注入、生命周期和持久化后端。建议在 Phase 1/2 稳定后再做。

## 新接入流程预期

目标流程：

```bash
python scripts/scaffold_agent.py \
  --id internship-assistant-zhanghaibo \
  --name 张海波实习记录助手 \
  --layer personal \
  --owner-scope user:wework:wework-main:direct:zhanghaibo
```

然后只维护：

```text
workspace/agents/internship-assistant-zhanghaibo/agent.yaml
workspace/agents/internship-assistant-zhanghaibo/IDENTITY.md
workspace/agents/internship-assistant-zhanghaibo/SOUL.md
workspace/agents/internship-assistant-zhanghaibo/TOOLS.md
```

如果使用已有工具，不再需要改：

- `config/agents.json`
- `agent_gateway/runtime/execution/collaboration.py`
- `agent_gateway/ai/tools/builtin.py` 的分类列表
- `agent_gateway/ai/agent_contracts.py`
- `workspace/agents/wework-entry/*`
- `workspace/agents/personal-secretary-zhanghaibo/*`
- 路由样例总数断言

如果引入全新工具，仍需要新增工具实现和注册方式；Phase 3 完成后也可以由 tool bundle 简化。

## 关键设计细节

### 1. Manifest schema 必须严格

建议校验：

- `id` 与目录名一致。
- `prompt` 文件存在。
- `tools.names` 中的工具全部已注册。
- `routing.aliases` 不和其他 Agent 冲突。
- `routing.keywords` 不能为空，除非该 Agent 不参与自动路由。
- 写入类 contract 必须声明 `requires_confirmation=true` 或解释为什么不需要。
- `owner_scope` 存在时，`extra_system` 或自动系统提示必须注入该 scope。

### 2. 路由仍要可解释

不要把路由完全交给模型自由判断。Manifest 中的 `keywords/reason/next` 能让 `classify_task_intent` 输出稳定、可测试、可解释的结果。后续可以加 embedding rerank，但关键词和契约仍应作为底线。

### 3. Prompt 使用自动注入块

入口 Agent prompt 不应手写所有下游 Agent。可以在 PromptAssembler 阶段注入：

```markdown
## 当前可委托 Agent

- `diet-assistant-zhanghaibo`：饮食、体重、热量、餐食记录。
- `internship-assistant-zhanghaibo`：实习记录、日报、导师反馈、项目卡点。
```

该内容从 manifest routing catalog 生成。这样新增 Agent 不需要修改入口 prompt。

### 4. 契约测试从 manifest 生成

`DEFAULT_AGENT_ROUTING_CONTRACTS` 可以拆成两类：

- 核心系统契约：repo、research、planner、reviewer、doc-writer、ops 等基础能力。
- Agent manifest 契约：每个 Agent 自己声明的 examples。

测试聚合两类契约即可，避免每新增 Agent 都改 Python 常量和测试数量。

### 5. 保留显式绑定

`config/bindings.json` 仍应保留，因为平台 peer 绑定属于部署拓扑，不是 Agent 能力本身。新增专科 Agent 默认不直接绑定 peer，只由入口/秘书/协作主控委托。Manifest 可以声明建议绑定，但不应自动写入生产绑定。

## 风险和取舍

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 迁移时新旧配置冲突 | 同一 Agent 同时存在于 `config/agents.json` 和 manifest | loader 明确优先级，重复 id 默认报错或要求字段一致 |
| 路由误命中 | 关键词过宽，例如“日报”可能是工作日报也可能是系统报告 | routing 支持 priority、negative_keywords、scope 限制 |
| Prompt 自动注入变长 | Agent 多了以后入口 prompt 变大 | 只注入 id、短职责、关键词摘要，不注入完整 prompt |
| 工具 bundle 动态导入风险 | 动态导入可能掩盖启动错误 | doctor 和启动阶段强校验，失败即阻断 |
| 契约过度依赖 manifest 自测 | Agent 自己声明的样例可能太窄 | 保留全局路由回归集，真实失败样例仍要沉淀 |

## 推荐最小实现

优先做 Phase 1 和 Phase 2 的最小闭环：

1. 新增 `AgentManifest` dataclass 和 `load_agent_manifests(workspace_root)`。
2. 给 `diet-assistant-zhanghaibo` 和 `internship-assistant-zhanghaibo` 各补一个 `agent.yaml` 作为试点。
3. `list_agent_capabilities()` 改用 manifest 的 `layer/routing`，没有 manifest 时回退旧逻辑。
4. `_normalize_delegate_target()` 从 manifest aliases 构建 alias map，保留旧 aliases 作为 fallback。
5. `_should_persist_delegate_history()` 从 manifest 字段读取。
6. `DEFAULT_AGENT_ROUTING_CONTRACTS` 增加 manifest examples 聚合函数，先不删除原常量。
7. `scripts/check_agent_manifests.py` 校验 schema、工具存在和 alias 冲突。
8. 增加一个测试：新增临时 manifest 后，不改 Python 代码也能出现在能力目录并被 alias 归一。

这个最小实现可以先把“新增使用已有工具的专科 Agent”从多处改动降到：

- 新增 prompt 目录。
- 新增 `agent.yaml`。
- 运行校验和路由评估。

## 结论

当前 Agent 管理复杂的根因不是配置文件多，而是同一份 Agent 元数据被分散复制到配置、prompt、Python 路由、能力目录、契约和测试中。最有效的简化方式是引入 **Agent Manifest 作为单一权威源**，再由 loader 自动派生 AgentConfig、能力目录、关键词路由、协作别名、历史持久化策略和路由契约。

短期建议先做 manifest + catalog 驱动路由，保持 `config/agents.json` 兼容；中期再把工具 store/register 也包化成 tool bundle。这样可以在不牺牲可解释性和测试门禁的前提下，把新增 Agent 的接入成本从“改 6-10 个位置”降到“新增一个目录和一个 manifest”。
