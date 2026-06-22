---
name: video-editing
description: 处理视频剪辑、长视频切短、教程视频、产品演示、Vlog、字幕、旁白、音频归一化、平台比例转换和 FFmpeg/Remotion 辅助剪辑流程。
invocation: /video-edit
---

# 视频剪辑

当用户要剪视频、拆分长录屏、做教程、做产品演示、生成字幕、添加旁白、转竖屏或整理剪辑流程时，使用本技能。

## 核心思路

不要让 AI “凭空生成完整视频”。更实用的方式是：用 AI 压缩、组织和增强真实素材。

推荐流水线：

```text
原始视频 / 录屏
→ 转写与结构规划
→ FFmpeg 确定性剪切
→ Remotion 做字幕/动效/标注
→ 音频与旁白处理
→ CapCut / Descript 做最后审美调整
```

## 常用任务

### 从长视频提取片段

先根据转写稿找出保留片段，再生成 FFmpeg 命令：

```bash
ffmpeg -i raw.mp4 -ss 00:12:30 -to 00:15:45 -c copy segment_01.mp4
```

### 拼接片段

```bash
printf "file '%s'\n" segments/*.mp4 > concat.txt
ffmpeg -f concat -safe 0 -i concat.txt -c copy assembled.mp4
```

### 提取音频用于转写

```bash
ffmpeg -i raw.mp4 -vn -acodec pcm_s16le -ar 16000 audio.wav
```

### 音频响度归一化

```bash
ffmpeg -i input.mp4 -af loudnorm=I=-16:TP=-1.5:LRA=11 -c:v copy normalized.mp4
```

### 平台比例转换

```bash
# 16:9 转 9:16
ffmpeg -i input.mp4 -vf "crop=ih*9/16:ih,scale=1080:1920" vertical.mp4
```

## 输出格式

根据输入材料返回：

- `剪辑方案`：保留哪些段落、删掉哪些段落、顺序如何安排。
- `剪辑命令`：可执行 FFmpeg 命令。
- `脚本 / 分镜`：适合教程、短视频、产品演示。
- `发布版本建议`：YouTube、B站、抖音、视频号等不同规格。

## 安全规则

- 不覆盖原始素材。
- 所有输出写到新目录，例如 `exports/` 或 `segments/`。
- 如果命令可能耗时或占空间，先说明影响。
