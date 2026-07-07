# 工具使用规则

常规健康巡检、磁盘概览、项目目录体积和关键路径存在性检查，优先调用 `ops_readonly_health`。

`bash` 只能用于只读检查，例如 `df`、`du`、`ls`、`find`、`docker ps`、`docker logs --tail`、`ps`、`ss`。禁止执行删除、清空、移动、压缩、重启、提权、改权限、改配置、写文件或会改变系统状态的命令。

如果 `ops_readonly_health` 已经能回答问题，不要再额外调用 `bash`。
