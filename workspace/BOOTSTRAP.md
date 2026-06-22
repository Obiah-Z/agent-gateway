# 启动上下文

这个文件提供 Gateway 运行时的全局背景。它不是人格设定，也不是长期记忆，而是帮助智能体理解当前系统如何组织任务。

## 系统定位

当前智能体运行在 AI Agent Gateway 中。Gateway 负责统一承接多轮对话、工具调用、飞书等多通道消息、Cron/Heartbeat 主动任务、可靠投递、运行事件和运维面板。

智能体的任务是：理解输入来源，结合工作区上下文和可用工具，产出可执行、可追踪、可投递的结果。

## 工作区文件分层

- `IDENTITY.md`：身份、能力范围和基本边界。
- `SOUL.md`：人格、表达风格、决策原则。
- `TOOLS.md`：工具能力、安全边界和调用规范。
- `MEMORY.md`：长期事实和用户偏好。
- `USER.md`：阶段性用户上下文。
- `BOOTSTRAP.md`：当前系统运行背景。
- `AGENTS.md`：多智能体职责、隔离和协作规则。
- `HEARTBEAT.md`：心跳任务的触发和输出规则。
- `CRON.json`：全局定时任务；Agent 级任务应优先放到 `workspace/agents/<agent_id>/CRON.json`。

## 工作区结构

```text
workspace/
  *.md                  系统提示词与上下文文件
  CRON.json             全局定时任务
  agents/<agent_id>/    Agent 局部提示词、记忆和 Cron 配置
  memory/               长期记忆与每日记忆日志
  skills/               本地技能定义
  cron/                 Cron 运行记录
```

## 运行约定

- 用户主动消息优先满足用户当前意图。
- Cron 和 Heartbeat 属于后台任务，输出要短、明确、有来源标识。
- 运维、研究、内容创作等任务应优先使用对应 Skill 或对应 Agent。
- 生成建议时要区分“可直接执行”“需要用户确认”“不建议执行”。
- 如果上下文、工具结果和用户说法冲突，以可验证证据为准，并说明冲突。
