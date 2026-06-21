---
name: server-space-advisor
description: 只读分析服务器磁盘占用、大文件、日志、缓存、虚拟环境和构建产物，并输出“可安全清理 / 需确认 / 不建议动”的中文清理建议。该技能只能检查和建议，禁止删除、移动、压缩、清空或修改文件。
invocation: /space-advisor
---

# 服务器空间清理建议

当用户要求分析磁盘空间、排查磁盘告警、寻找大文件、整理日志/缓存/构建产物，或 Cron 定时触发空间巡检时，使用本技能。

## 强约束

- 只做分析，不做清理。
- 禁止执行 `rm`、`find -delete`、`docker system prune`、`journalctl --vacuum-*`、`apt clean`、`pip cache purge`、`npm cache clean`、日志截断、移动文件、压缩文件等修改性动作。
- 优先使用本技能目录下的只读脚本，不要手写危险命令。
- 如果权限不足或扫描不完整，直接说明“扫描不完整”，不要尝试提权。

## 标准执行

优先通过 `bash` 工具在 workspace 根目录执行：

```bash
python3 skills/server-space-advisor/scripts/space_advisor.py --paths / /home /var /tmp --depth 2 --limit 20 --json
```

如果只需要检查当前项目：

```bash
python3 skills/server-space-advisor/scripts/space_advisor.py --paths /home/obiah/Desktop/claw0/gateway --depth 2 --limit 20 --json
```

## 输出要求

用中文输出，固定分成四段：

- `磁盘概览`：说明最紧张分区、使用率、扫描是否完整。
- `可安全清理`：列出缓存、构建产物、旧临时文件等低风险对象。
- `需确认`：列出虚拟环境、下载文件、归档、备份、Docker 数据、业务目录等需要用户确认的对象。
- `不建议动`：列出系统目录、数据库目录、凭据目录、源码仓库、运行中服务数据等不应直接处理的对象。

每个建议必须包含路径、大小、原因和下一步确认点。
