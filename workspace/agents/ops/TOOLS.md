# 工具使用规则

常规健康巡检、磁盘概览、项目目录体积和关键路径存在性检查，优先调用 `ops_readonly_health`。

拿到 `ops_readonly_health` 的 JSON 后，调用 `summarize_ops_health`。输出必须包含风险等级、关键发现、安全建议和需要用户手动确认的动作。

排查最近运行错误、通道拒绝、失败投递、告警历史和事件流异常时，优先调用 `ops_runtime_diagnostics`。该工具只读读取本地 JSONL 和队列状态，不会清空事件、删除失败投递或修改配置。

需要把健康摘要和运行诊断合并成“先查什么、后查什么”的排障顺序时，使用 `ops_troubleshooting_plan`。该工具只输出只读检查步骤、安全命令建议和必须人工确认的动作，不会执行清理、重启、重放、删除或改配置。

`bash` 只能用于只读检查，例如 `df`、`du`、`ls`、`find`、`docker ps`、`docker logs --tail`、`ps`、`ss`。禁止执行删除、清空、移动、压缩、重启、提权、改权限、改配置、写文件或会改变系统状态的命令。

如果 `ops_readonly_health` 已经能回答问题，不要再额外调用 `bash`。
