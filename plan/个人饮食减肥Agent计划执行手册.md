# 个人饮食减肥 Agent 计划执行手册

本文档用于指导后续把“个人饮食减肥 Agent”从方案落到 Gateway 项目中。它不是产品说明，而是面向开发、测试和上线的执行手册。原始方案参考 `doc/个人饮食减肥Agent实施方案.md`，本手册按实际工程推进顺序拆解任务。

## 一、建设目标

目标是在现有 Gateway 体系中新增一个面向个人用户的饮食与体重管理 Agent。它需要支持用户档案持久化、每日饮食记录、热量估算、每日饮食计划、饭点提醒、晚间汇总和长期减肥计划调整。

系统必须满足三个基本原则。

第一，个人信息必须按用户隔离。所有用户档案、饮食记录、体重记录、计划和汇总都必须带 `user_scope`，工具层和数据库查询默认使用当前会话派生出的用户作用域，禁止不同用户共享同一份个人数据。

第二，饮食数据必须结构化持久化。长期偏好可以进入记忆系统，但每日餐食、热量、体重和计划不能只写入 Markdown 或普通 memory，需要进入 PostgreSQL 表，便于按日期统计、生成趋势和做幂等控制。

第三，建议必须实用且保守。Agent 只做生活方式辅助，不做医疗诊断；涉及疾病、孕期、药物、进食障碍、极端节食等情况时，只提供记录和温和建议，并提示用户咨询医生或营养师。

## 二、最终交付形态

用户首次使用时，Agent 会收集身高、体重、目标体重、活动水平、忌口、饮食偏好和常见用餐场景。用户可以跳过部分字段，系统用保守默认值并标记置信度较低。

每天 06:00，Agent 给指定用户推送当天饮食计划，内容包括目标热量、早餐/午餐/晚餐/加餐建议、替换规则和当天注意事项。

每天饭点前后，Agent 主动提醒用户记录实际吃了什么。饭前提醒以计划为主，饭后提醒以补录为主。如果该餐已经记录，则不重复催促。

每天 22:00，Agent 汇总当天摄入情况，包括总热量、蛋白质、碳水、脂肪、与目标差值、缺失餐次、做得好的地方和明天一条可执行调整建议。

用户随时可以通过企业微信或飞书补录餐食、更新体重、修改目标、查询最近 7 天或 30 天趋势、询问食物替代方案。

## 三、实施里程碑

### M1：数据底座与用户隔离

目标是把个人饮食 Agent 所需的结构化数据落到 PostgreSQL，并确保所有读写都绑定 `user_scope`。

需要完成的任务包括新增数据库表、补充 repository 方法、实现基础查询和写入测试。该阶段不要求完成完整 Agent 交互，但必须能通过代码直接写入和读取用户档案、餐食记录、体重记录、每日汇总和饮食计划。

验收标准是两个不同 `user_scope` 写入相似餐食后，互相查询不到对方数据；同一用户可以按日期查询自己的餐食和汇总。

### M2：饮食工具层

目标是为模型提供安全、可控的工具接口。工具层负责参数校验、默认日期、用户作用域注入、数据库读写和错误兜底，模型只负责理解用户输入和组织回复。

第一批工具包括 `profile_get`、`profile_update`、`meal_log_add`、`meal_log_list`、`nutrition_day_summary`、`diet_plan_generate`、`weight_log_add`、`progress_summary`。

验收标准是模型不需要知道 SQL，也不需要手动传 `user_scope`，通过当前会话即可写入当前用户的数据。

### M3：企业微信交互闭环

目标是让用户可以在企业微信中完成首次建档、记录一餐、查询当日摄入和收到结构化回复。

该阶段先做同步交互，不急于做全部定时任务。需要配置一个专用 Agent，例如 `diet-assistant`，并绑定企业微信个人会话。Prompt 要明确“先记录事实，再给建议”，不要把每日饮食写入普通 memory。

验收标准是用户在企业微信发送“中午吃了牛肉饭一份、无糖可乐”，系统能调用 `meal_log_add` 落库，并回复估算热量、置信度和可修正提示。

### M4：定时任务与主动提醒

目标是接入每日 06:00 计划、饭点提醒和 22:00 汇总。所有提醒必须支持幂等，避免多实例部署时重复推送。

建议新增专用 task type，而不是只靠普通 `agent_turn`。专用 task type 先读取数据库生成结构化输入，再交给 Agent 润色，最后通过现有可靠投递链路发出。

验收标准是同一个用户同一天同一提醒阶段只会收到一次消息；任务失败后可以重试；重启后不会重复发送已经成功投递的提醒。

### M5：个性化计划与运营面板

目标是根据连续记录调整计划，并在 Dashboard 中查看关键状态。

需要支持最近 7 天和 30 天趋势、缺失餐次、连续超标、蛋白质不足、体重变化和提醒执行状态。Dashboard 不展示跨用户明细混合列表，只能按用户或聚合指标查看。

验收标准是连续多天晚餐超标后，次日计划能自动给出更轻量、仍可执行的晚餐替代方案。

## 四、数据模型执行清单

### `user_profiles`

用于保存用户基本档案。建议字段包括 `user_scope`、`display_name`、`gender`、`birth_year`、`height_cm`、`current_weight_kg`、`target_weight_kg`、`activity_level`、`timezone`、`diet_preferences`、`allergies`、`medical_notes`、`created_at`、`updated_at`、`metadata`。

关键约束是 `user_scope` 唯一。用户每次更新体重时，可以同步更新 `current_weight_kg`，但历史体重必须进入 `weight_logs`。

### `weight_logs`

用于保存体重记录。建议字段包括 `id`、`user_scope`、`weight_kg`、`recorded_at`、`source`、`metadata`。

查询需要支持按用户、时间范围倒序读取。后续周报和趋势图依赖该表。

### `meal_logs`

用于保存每餐记录。建议字段包括 `id`、`user_scope`、`meal_date`、`meal_type`、`raw_text`、`items`、`estimated_calories`、`protein_g`、`carbs_g`、`fat_g`、`confidence`、`logged_at`、`metadata`。

`items` 建议用 JSONB，记录食物名称、份量文本、估算热量、蛋白质、估算依据和置信度。`raw_text` 必须保留，便于用户后续纠错和审计。

### `daily_nutrition_summaries`

用于保存每日汇总。建议字段包括 `id`、`user_scope`、`date`、`target_calories`、`actual_calories`、`protein_g`、`carbs_g`、`fat_g`、`summary_text`、`risk_flags`、`created_at`、`updated_at`、`metadata`。

同一用户同一天建议唯一，允许重复生成时 upsert 覆盖，并保留 `metadata.regenerated_count` 或事件日志记录。

### `diet_plans`

用于保存每日饮食计划。建议字段包括 `id`、`user_scope`、`plan_date`、`target_calories`、`meals`、`shopping_tips`、`generated_reason`、`status`、`created_at`、`metadata`。

`meals` 建议用 JSONB 保存早餐、午餐、晚餐和加餐。每餐至少包含两个可替换选项，避免用户因为固定菜单难以执行。

## 五、工具层执行清单

所有工具都必须自动读取当前运行上下文中的 `memory_user_scope` 或后续统一的 `user_scope`，禁止让模型自由决定用户 ID。

`profile_get` 返回当前用户档案。如果档案不存在，返回缺失字段列表和首次建档提示。

`profile_update` 更新用户档案。它需要做范围校验，例如身高、体重、目标体重不能是明显异常值。

`meal_log_add` 保存餐食记录。输入包括 `meal_type`、`raw_text`、`items`、`estimated_calories`、`protein_g`、`carbs_g`、`fat_g`、`confidence`。第一版可以由模型先抽取 JSON，再由工具落库。

`meal_log_list` 查询某天或某个时间范围内的餐食记录，默认查询今天。

`nutrition_day_summary` 统计某天摄入。它需要明确缺失餐次，不允许在没有记录时假装准确。

`diet_plan_generate` 根据用户档案、目标、历史记录和最近执行情况生成计划，并写入 `diet_plans`。

`weight_log_add` 保存体重记录，并同步更新 `user_profiles.current_weight_kg`。

`progress_summary` 输出最近 7 天或 30 天趋势，用于用户主动查询和周报。

## 六、Cron 与任务编排

建议把饮食 Agent 的主动任务配置为“按用户定向”的任务，不要发给企业微信中的所有用户。

每日 06:00 运行 `diet_plan_generate`，幂等键建议为 `diet-plan:{user_scope}:{date}`。

早餐前 07:30 发送早餐计划提醒，幂等键建议为 `meal-reminder:{user_scope}:{date}:breakfast:before`。

早餐后 09:30 检查早餐是否已记录，未记录才发送补录提醒，幂等键建议为 `meal-reminder:{user_scope}:{date}:breakfast:after`。

午餐前 11:30、午餐后 13:30、晚餐前 17:30、晚餐后 20:00 采用同样模式。

每日 22:00 运行 `nutrition_day_summary`，幂等键建议为 `nutrition-summary:{user_scope}:{date}`。

所有主动任务都应进入现有任务队列和可靠投递链路。任务执行失败时允许重试，但发送前必须检查幂等状态，避免重复推送。

## 七、Agent 配置建议

建议新增 `diet-assistant` Agent。它的定位是“个人饮食与体重管理助手”，不是医疗顾问。

Prompt 需要包含以下约束。先记录事实，再给建议；热量估算必须标注不确定性；每日餐食写入 `meal_logs`，不要写入普通 memory；长期偏好和稳定事实才允许写入 memory；建议必须简单、可执行、低压力；不要承诺快速减重；不要鼓励极端节食。

工具白名单建议只开放饮食相关工具、必要的时间工具和受控记忆工具。第一版不要开放 bash、文件写入等无关工具。

## 八、测试计划

单元测试需要覆盖用户隔离、表写入、工具参数校验、餐食统计、计划生成和幂等键生成。

集成测试需要覆盖企业微信入站到工具调用再到出站回复的闭环。至少准备两个不同用户，验证相同日期下的餐食和计划不会串号。

定时任务测试需要覆盖 06:00 计划、饭点前提醒、饭点后补录提醒和 22:00 汇总。对于已记录餐次，饭后提醒应跳过。

可靠性测试需要覆盖重启恢复、任务重试、重复事件、重复 Cron 和投递失败。核心结论是重复执行不会造成重复推送或重复写入。

## 九、上线步骤

第一步，新增数据库 schema 和 repository，运行单元测试。

第二步，新增饮食工具并接入 ToolRegistry，运行工具层测试。

第三步，新建 `diet-assistant` Agent 配置和 Prompt，先只做手动对话。

第四步，在企业微信中绑定指定用户或指定 peer_id，验证手动记录餐食和查询汇总。

第五步，开启 06:00 和 22:00 两个低频 Cron，观察三天。

第六步，再开启饭点前后提醒，并加入用户级提醒开关和免打扰时间。

第七步，接入 Dashboard 展示最近饮食记录、今日热量、缺失餐次、任务执行状态和投递状态。

## 十、验收标准

用户首次对话可以完成建档或返回清晰的缺失字段提示。

用户可以通过企业微信记录一餐，记录会写入 PostgreSQL，并能按当天查询出来。

两个不同用户的数据完全隔离，互相不会出现在对方的召回、汇总、计划或 Dashboard 明细中。

每日 06:00 可以给指定用户生成当天计划。

饭点前后可以提醒用户记录餐食，已记录餐次不会重复提醒。

每日 22:00 可以生成当天摄入汇总，并明确缺失餐次和估算不确定性。

计划建议保持实用，不包含极端节食、医疗诊断或不负责任的减重承诺。

多实例或重启场景下，重复 Cron、重复事件和重试不会导致重复发送同一提醒。

## 十一、风险与回滚

如果热量估算不稳定，先降低自动建议强度，只做记录和汇总，并在回复中突出“估算值，可修正”。

如果定时提醒打扰用户，默认关闭饭点提醒，只保留 06:00 计划和 22:00 汇总，等用户主动开启。

如果出现用户数据串号，立即关闭饮食工具和 Cron，保留数据库，排查 `user_scope` 派生、工具上下文注入和查询过滤。

如果投递重复，优先检查幂等键、任务状态表和可靠投递队列，必要时暂停主动任务，只保留手动交互。

## 十二、推荐执行顺序

推荐先完成 M1 和 M2，因为它们决定后续所有功能是否可靠。不要先写复杂 Prompt 或 Cron，否则数据隔离和统计能力会变成后补，风险较高。

M3 完成后可以开始小范围自用。M4 再开启主动提醒。M5 属于体验增强，不应阻塞第一版上线。

## 十三、当前指定用户的专属 Agent 落地方案

本节把方案收敛到当前指定用户，并记录已经落地到项目中的具体配置。这个 Agent 不是企业微信全员 Agent，而是绑定到单个企业微信 `peer_id` 的个人饮食减肥助手。

用户给出的会话标识是 URL 编码后的 session key。

```text
agent%3Awework-main%3Awework%3Awework-main%3Adirect%3Add5bf6254c5b565c1f59edf6b29aa30c
```

解码后为：

```text
agent:wework-main:wework:wework-main:direct:dd5bf6254c5b565c1f59edf6b29aa30c
```

按照当前 Gateway 的记忆作用域派生规则，去掉 `agent_id` 后，该用户的个人数据作用域应固定为：

```text
user:wework:wework-main:direct:dd5bf6254c5b565c1f59edf6b29aa30c
```

企业微信主动投递目标应固定为：

```text
channel: wework
account_id: wework-main
peer_id: dd5bf6254c5b565c1f59edf6b29aa30c
```

### 已新建的专属 Agent

当前 `wework-main` 是企业微信账号级个人秘书 Agent，`config/bindings.json` 里已经存在 `account_id=wework-main` 的通用绑定。饮食减肥 Agent 有强个人属性、定时提醒和长期饮食数据，因此已经新建专属 Agent，避免影响同一个企业微信账号下的其他用户。

当前专属 Agent 为：

```text
agent_id: diet-assistant-dd5bf625
name: 个人饮食减肥助手
prompt_dir: agents/diet-assistant-dd5bf625
dm_scope: per-account-channel-peer
```

这里 `dd5bf625` 只作为可读短后缀，真实用户隔离仍以完整 `user_scope` 为准。

### 已落地的 Agent 配置

`config/agents.json` 中已经新增 `diet-assistant-dd5bf625`。它使用独立 prompt 目录，并只开放饮食管理需要的工具。

```json
{
  "id": "diet-assistant-dd5bf625",
  "name": "个人饮食减肥助手",
  "personality": "克制、实用、长期陪伴的个人饮食与体重管理助手",
  "model": "",
  "dm_scope": "per-account-channel-peer",
  "extra_system": "你只服务当前指定用户，目标是帮助用户记录饮食、估算热量、形成可执行的减脂计划。不要做医疗诊断，不要建议极端节食。每日餐食、体重和计划必须写入饮食专用数据表；只有长期稳定偏好才允许写入 memory。回复要简洁、具体、可执行。",
  "tool_policy": {
    "mode": "allowlist",
    "tool_names": [
      "get_current_time",
      "profile_get",
      "profile_update",
      "meal_log_add",
      "meal_log_list",
      "nutrition_day_summary",
      "diet_plan_generate",
      "weight_log_add",
      "progress_summary",
      "memory_search",
      "memory_write"
    ]
  }
}
```

### 精确路由绑定

`config/bindings.json` 中已经新增 peer 级绑定，优先级高于现有 `account_id=wework-main` 的通用绑定。这样该用户的企业微信消息会进入饮食 Agent，其他企业微信用户仍走原来的 `wework-main` 个人秘书。

当前精确绑定为：

```json
{
  "_comment": "指定企业微信用户的个人饮食减肥 Agent。只匹配该用户 peer_id，避免影响 wework-main 账号下的其他用户。",
  "agent_id": "diet-assistant-dd5bf625",
  "tier": 1,
  "match_key": "peer_id",
  "match_value": "dd5bf6254c5b565c1f59edf6b29aa30c",
  "priority": 150
}
```

现有 `wework-main` 账号级绑定可以保留：

```json
{
  "agent_id": "wework-main",
  "tier": 3,
  "match_key": "account_id",
  "match_value": "wework-main",
  "priority": 50
}
```

匹配结果会变成：该指定用户命中 tier 1 饮食 Agent，其他 `wework-main` 用户继续命中 tier 3 通用秘书 Agent。

### 专属 Prompt 目录

已经新增专属 Prompt 目录：

```text
workspace/agents/diet-assistant-dd5bf625/
```

目录中包含：

```text
IDENTITY.md
SOUL.md
TOOLS.md
USER.md
CRON.json
```

`IDENTITY.md` 固定身份和用户作用域。`SOUL.md` 约束工作风格，避免焦虑化和医疗化建议。`TOOLS.md` 明确每日餐食、体重、计划和汇总必须使用饮食工具写入结构化数据。`USER.md` 只保留少量非结构化补充，不记录每日餐食。

### 专属 Cron 文件

CronService 会加载 `workspace/agents/<agent_id>/CRON.json`，并自动把 owner agent 设置为该目录名。因此该用户的主动任务已经放在：

```text
workspace/agents/diet-assistant-dd5bf625/CRON.json
```

不要把这个用户的饮食任务放到全局 `workspace/CRON.json`。全局 Cron 容易被误认为所有用户共享任务，也更容易误发。

当前默认只开启两个低频任务：06:00 饮食计划和 22:00 晚间汇总。饭点前后提醒已经写入配置，但默认 `enabled: false`，等用户确认提醒频率后再逐个打开。

```json
{
  "jobs": [
    {
      "id": "daily-diet-plan",
      "name": "每日饮食计划",
      "enabled": true,
      "schedule": {
        "kind": "cron",
        "expr": "0 6 * * *",
        "tz": "Asia/Shanghai"
      },
      "target": {
        "channel": "wework",
        "account_id": "wework-main",
        "peer_id": "dd5bf6254c5b565c1f59edf6b29aa30c",
        "agent_id": "diet-assistant-dd5bf625"
      },
      "payload": {
        "kind": "diet_plan_generate",
        "user_scope": "user:wework:wework-main:direct:dd5bf6254c5b565c1f59edf6b29aa30c",
        "date": "today"
      },
      "delete_after_run": false
    },
    {
      "id": "daily-nutrition-summary",
      "name": "晚间热量汇总",
      "enabled": true,
      "schedule": {
        "kind": "cron",
        "expr": "0 22 * * *",
        "tz": "Asia/Shanghai"
      },
      "target": {
        "channel": "wework",
        "account_id": "wework-main",
        "peer_id": "dd5bf6254c5b565c1f59edf6b29aa30c",
        "agent_id": "diet-assistant-dd5bf625"
      },
      "payload": {
        "kind": "nutrition_day_summary",
        "user_scope": "user:wework:wework-main:direct:dd5bf6254c5b565c1f59edf6b29aa30c",
        "date": "today"
      },
      "delete_after_run": false
    }
  ]
}
```

饭点提醒已作为第二阶段任务保留，当前不会主动发送。

```json
{
  "id": "breakfast-after-reminder",
  "name": "早餐补录提醒",
  "enabled": false,
  "schedule": {
    "kind": "cron",
    "expr": "30 9 * * *",
    "tz": "Asia/Shanghai"
  },
  "target": {
    "channel": "wework",
    "account_id": "wework-main",
    "peer_id": "dd5bf6254c5b565c1f59edf6b29aa30c",
    "agent_id": "diet-assistant-dd5bf625"
  },
  "payload": {
    "kind": "meal_reminder",
    "user_scope": "user:wework:wework-main:direct:dd5bf6254c5b565c1f59edf6b29aa30c",
    "date": "today",
    "meal_type": "breakfast",
    "stage": "after"
  },
  "delete_after_run": false
}
```

### 为什么这样不会发给所有用户

这里有四层限制。

第一，入站路由使用 `peer_id=dd5bf6254c5b565c1f59edf6b29aa30c` 的 tier 1 精确绑定，只把该用户消息交给 `diet-assistant-dd5bf625`。

第二，Cron 文件放在 `workspace/agents/diet-assistant-dd5bf625/CRON.json`，任务作用域是这个专属 Agent，而不是全局任务。

第三，每个 Cron job 的 `target.peer_id` 都固定为 `dd5bf6254c5b565c1f59edf6b29aa30c`，出站投递只会发给这个企业微信用户。

第四，饮食数据查询和写入固定使用 `user:wework:wework-main:direct:dd5bf6254c5b565c1f59edf6b29aa30c`，即使未来有其他用户使用同一个 Agent 模板，也不会读到该用户的数据。

### 当前 Cron 执行方式

饮食 Cron 已经不再使用普通 `agent_turn` 作为核心执行方式，而是使用饮食专用 payload：

```json
{
  "kind": "diet_plan_generate",
  "user_scope": "user:wework:wework-main:direct:dd5bf6254c5b565c1f59edf6b29aa30c",
  "date": "today"
}
```

`CronService` 会直接读取 `user_scope`、`date`、`meal_type` 和 `stage`，再调用 `DietStore` 生成计划、汇总或提醒文本。饭后提醒会先查询 `meal_logs`，对应餐次已经记录时直接跳过，不再依赖模型从 prompt 中理解“不要提醒”。

Redis 可用时，饮食 Cron 会使用以下幂等键避免多实例或重试重复推送：

```text
diet-plan:{user_scope}:{date}
nutrition-summary:{user_scope}:{date}
meal-reminder:{user_scope}:{date}:{meal_type}:{stage}
```

## 十四、当前工程落地状态

当前已经完成专属 Agent 的基础可用版本。配置层已经把指定企业微信用户路由到 `diet-assistant-dd5bf625`，主动任务层已经把 06:00 饮食计划和 22:00 晚间汇总固定投递给 `peer_id=dd5bf6254c5b565c1f59edf6b29aa30c`，不会发送给企业微信中的其他用户。

饮食数据层已经新增结构化存储能力。`user_profiles` 保存用户档案，`meal_logs` 保存每日餐食，`weight_logs` 保存体重记录，`daily_nutrition_summaries` 保存每日汇总，`diet_plans` 保存每日饮食计划。所有表都以 `user_scope` 作为隔离边界，当前用户固定为 `user:wework:wework-main:direct:dd5bf6254c5b565c1f59edf6b29aa30c`。

工具层已经注册 `profile_get`、`profile_update`、`meal_log_add`、`meal_log_list`、`nutrition_day_summary`、`diet_plan_generate`、`weight_log_add`、`progress_summary`。模型不需要直接操作 SQL，工具默认从运行上下文读取当前用户作用域；Cron 场景使用结构化 payload 显式携带固定 `user_scope`，由服务层调用 `DietStore`。

Dashboard 和控制面已经增加饮食观察能力。未指定 `user_scope` 时只展示用户级摘要，避免把不同用户的私密餐食明细混在一个列表里；指定 `user_scope` 后会展示该用户今日状态卡和最近餐食、计划、汇总明细。今日状态卡包含今日热量、目标热量、蛋白质、缺失餐次、最新体重、档案完整度、风险标记和近 7 天趋势摘要。

个性化计划已经具备第一版动态调整能力。`diet_plan_generate` 会读取近期餐食记录，如果近期多次晚餐热量偏高，会自动把次日晚餐建议调整为更轻量的蛋白质、蔬菜和半拳主食组合；如果近期蛋白质偏低，会提高午餐和加餐的蛋白质优先级；如果早餐记录偏少，会给出更容易执行的固定早餐组合。调整原因会写入 `diet_plans.metadata.adjustment`。

## 十五、验证清单

上线前需要执行以下检查。第一，运行 JSON 校验，确认 `workspace/agents/diet-assistant-dd5bf625/CRON.json` 是合法 JSON。第二，运行配置测试，确认 `diet-assistant-dd5bf625` 存在、工具白名单完整、peer 级绑定优先于 `wework-main` 通用绑定。第三，运行饮食工具测试，确认不同 `user_scope` 的餐食、体重、计划和汇总互相隔离。第四，通过企业微信给该用户发送一条餐食记录，例如“中午吃了牛肉饭一份、无糖可乐”，确认回复中包含热量估算，并且 PostgreSQL 中能查到对应 `meal_logs` 记录。第五，确认其他企业微信用户仍路由到 `wework-main`，不会收到饮食计划或晚间汇总。

## 十六、当前已完成与下一步增强

已完成 Cron 从普通 `agent_turn` 升级为饮食专用 payload。`diet_plan_generate`、`nutrition_day_summary` 和 `meal_reminder` 都由服务层读取结构化字段并执行，减少模型忘记调用工具或误读用户作用域的风险。

已加入饮食主动任务幂等键。Redis 可用时，同一用户、同一天、同一任务类型只会放行一次，避免多实例、重试或重启后重复推送。

已把饭后提醒改成条件触发。饭后提醒会先查询该餐是否已记录，已记录则跳过；饭前提醒会优先读取当天饮食计划中的对应餐次建议。

下一步可以继续增强两点。第一，把当前 7 天 / 30 天趋势摘要升级为可视化趋势图。第二，把个性化计划从规则型调整升级为周报型调整，结合周末摄入波动、体重趋势和用户反馈生成下一周策略。
