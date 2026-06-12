# 启动上下文

这个文件用于给智能体启动时补充额外上下文。

## 项目背景

当前智能体属于 `claw0` 教学工程的一部分，用来演示如何从零构建一个 AI Agent Gateway。
`workspace/` 目录中的文件会共同塑造智能体的行为：

- `SOUL.md`：人格和表达风格
- `IDENTITY.md`：角色定义与边界
- `TOOLS.md`：可用工具与使用说明
- `MEMORY.md`：长期事实与用户偏好
- `HEARTBEAT.md`：主动任务与后台检查规则
- `BOOTSTRAP.md`：当前文件，用于补充启动上下文
- `AGENTS.md`：多智能体协作说明
- `CRON.json`：定时任务定义

## 工作区结构

```text
workspace/
  *.md          系统提示词与上下文文件，会被加载进 system prompt
  CRON.json     定时任务配置
  memory/       每日记忆日志
  skills/       本地技能定义
  .sessions/    会话记录（自动管理）
  .agents/      每个智能体的运行状态（自动管理）
```
