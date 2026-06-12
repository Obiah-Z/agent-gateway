---
name: example-skill
description: 用于演示的示例技能
invocation: /example
---
# 示例技能

当用户输入 `/example` 时，请先友好地打招呼，然后说明这是一个从 `workspace/skills/` 目录动态加载的演示技能。

你可以在 `workspace/skills/` 下新建自己的技能目录，并在其中放置一个 `SKILL.md` 文件。
这个文件应包含 frontmatter，例如 `name`、`description`、`invocation`，以及后续的技能说明正文。
