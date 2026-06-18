"use strict";

class JsonRpcClient {
  constructor(url, hooks = {}) {
    this.url = url;
    this.hooks = hooks;
    this.socket = null;
    this.nextId = 1;
    this.pending = new Map();
  }

  connect() {
    this.close();
    this._setStatus("connecting", "正在连接", this.url);
    this.socket = new WebSocket(this.url);
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        this.socket?.removeEventListener("open", onOpen);
        this.socket?.removeEventListener("error", onError);
      };
      const onOpen = () => {
        cleanup();
        this._setStatus("connected", "已连接", this.url);
        resolve();
      };
      const onError = () => {
        cleanup();
        this._setStatus("error", "连接失败", this.url);
        reject(new Error(`WebSocket 连接失败：${this.url}`));
      };
      this.socket.addEventListener("open", onOpen);
      this.socket.addEventListener("error", onError);
      this.socket.addEventListener("message", (event) => this._handleMessage(event));
      this.socket.addEventListener("close", () => {
        this._rejectPending(new Error("WebSocket 已断开"));
        this._setStatus("closed", "已断开", this.url);
      });
    });
  }

  close() {
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this._rejectPending(new Error("WebSocket 已关闭"));
  }

  call(method, params = {}, timeoutMs = 30000) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("WebSocket 未连接"));
    }
    const id = this.nextId++;
    const request = { jsonrpc: "2.0", id, method, params };
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`调用超时：${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify(request));
    });
  }

  _handleMessage(event) {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch (error) {
      this.hooks.onError?.(new Error("收到无法解析的 JSON-RPC 响应"));
      return;
    }
    const slot = this.pending.get(payload.id);
    if (!slot) {
      return;
    }
    window.clearTimeout(slot.timer);
    this.pending.delete(payload.id);
    if (payload.error) {
      slot.reject(new Error(payload.error.message || "JSON-RPC 调用失败"));
      return;
    }
    slot.resolve(payload.result);
  }

  _rejectPending(error) {
    for (const slot of this.pending.values()) {
      window.clearTimeout(slot.timer);
      slot.reject(error);
    }
    this.pending.clear();
  }

  _setStatus(state, label, detail) {
    this.hooks.onStatus?.({ state, label, detail });
  }
}

const state = {
  client: null,
  autoRefreshTimer: null,
  deliveryState: "failed",
  refreshIntervalSeconds: 15,
  eventsExpanded: false,
};

const $ = (selector) => document.querySelector(selector);

const dom = {
  wsUrl: $("#ws-url"),
  connectBtn: $("#connect-btn"),
  refreshBtn: $("#refresh-btn"),
  autoRefresh: $("#auto-refresh"),
  alert: $("#alert"),
  connectionDot: $("#connection-dot"),
  connectionLabel: $("#connection-label"),
  connectionDetail: $("#connection-detail"),
  metricHealth: $("#metric-health"),
  metricHealthDetail: $("#metric-health-detail"),
  metricUptime: $("#metric-uptime"),
  metricServer: $("#metric-server"),
  metricDelivery: $("#metric-delivery"),
  metricDeliveryDetail: $("#metric-delivery-detail"),
  metricChannels: $("#metric-channels"),
  metricChannelsDetail: $("#metric-channels-detail"),
  metricProfiles: $("#metric-profiles"),
  metricProfilesDetail: $("#metric-profiles-detail"),
  metricCron: $("#metric-cron"),
  metricCronDetail: $("#metric-cron-detail"),
  healthSummary: $("#health-summary"),
  healthList: $("#health-list"),
  runtimeUpdated: $("#runtime-updated"),
  runtimeList: $("#runtime-list"),
  tracesSummary: $("#traces-summary"),
  tracesList: $("#traces-list"),
  eventComponent: $("#event-component"),
  eventStatus: $("#event-status"),
  eventCorrelation: $("#event-correlation"),
  eventsSummary: $("#events-summary"),
  eventsList: $("#events-list"),
  errorsSummary: $("#errors-summary"),
  errorsList: $("#errors-list"),
  memorySummary: $("#memory-summary"),
  memoryList: $("#memory-list"),
  deliveryState: $("#delivery-state"),
  includeText: $("#include-text"),
  deliveryFlushBtn: $("#delivery-flush-btn"),
  deliveryTable: $("#delivery-table"),
  cronSummary: $("#cron-summary"),
  cronList: $("#cron-list"),
  issueSummary: $("#issue-summary"),
  issueList: $("#issue-list"),
  toast: $("#toast"),
  deliveryDetail: $("#delivery-detail"),
  deliveryDetailBody: $("#delivery-detail-body"),
  deliveryDetailClose: $("#delivery-detail-close"),
};

const STATUS_LABELS = {
  ok: "正常",
  warning: "注意",
  degraded: "降级",
  critical: "严重",
  unhealthy: "异常",
  error: "错误",
  failed: "失败",
  rejected: "拒绝",
  ignored: "忽略",
  duplicate: "重复",
  pending: "待投递",
  sent: "已发送",
  discarded: "已丢弃",
  dead: "已终止",
  ready: "可重试",
};

const COMPONENT_LABELS = {
  dispatcher: "消息分发",
  agent_loop: "智能体执行",
  tools: "工具调用",
  delivery: "可靠投递",
  cron: "定时任务",
  feishu: "飞书接入",
  websocket: "控制面",
  model: "模型调用",
  profile: "模型 Profile",
  context: "上下文",
};

const EVENT_LABELS = {
  "inbound.received": "收到入站消息",
  "route.resolved": "完成路由匹配",
  "agent.turn.started": "开始智能体轮次",
  "agent.turn.completed": "智能体轮次完成",
  "agent.turn.failed": "智能体轮次失败",
  "agent.task.started": "后台任务进入执行",
  "tool.call.started": "开始调用工具",
  "tool.call.completed": "工具调用完成",
  "tool.call.failed": "工具调用失败",
  "delivery.enqueued": "消息已进入投递队列",
  "delivery.sent": "消息投递成功",
  "delivery.failed": "消息投递失败",
  "cron.triggered": "定时任务触发",
  "cron.completed": "定时任务完成",
  "cron.failed": "定时任务失败",
  "feishu.event.accepted": "飞书事件已接收",
  "feishu.event.ignored": "飞书事件已忽略",
  "feishu.event.rejected": "飞书事件已拒绝",
  "model.call.started": "开始调用模型",
  "model.call.completed": "模型调用完成",
  "model.call.failed": "模型调用失败",
  "profile.selected": "已选择模型 Profile",
  "profile.failed": "模型 Profile 失败",
  "profile.cooldown": "模型 Profile 冷却",
  "context.compacted": "上下文已压缩",
};

const DETAIL_LABELS = {
  agent_id: "智能体",
  session_key: "会话",
  channel: "通道",
  account_id: "账号",
  peer_id: "目标",
  delivery_id: "投递 ID",
  job_id: "任务",
  correlation_id: "链路 ID",
  component: "模块",
  metadata: "技术元数据",
  error: "错误详情",
};

function defaultWsUrl() {
  const saved = window.localStorage.getItem("gateway.dashboard.wsUrl");
  if (saved) {
    return saved;
  }
  if (window.location.protocol.startsWith("http") && window.location.hostname) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.hostname}:8765`;
  }
  return "ws://127.0.0.1:8765";
}

async function loadDashboardConfig() {
  if (!window.location.protocol.startsWith("http")) {
    return;
  }
  try {
    const response = await fetch("./dashboard-config.json", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const config = await response.json();
    if (config.websocket_url && !window.localStorage.getItem("gateway.dashboard.wsUrl")) {
      dom.wsUrl.value = config.websocket_url;
    }
    if (Number.isFinite(Number(config.refresh_interval_seconds))) {
      state.refreshIntervalSeconds = Math.max(3, Number(config.refresh_interval_seconds));
    }
  } catch (error) {
    // The page still works when opened directly from disk.
  }
}

function setConnectionStatus({ state: connectionState, label, detail }) {
  dom.connectionLabel.textContent = label;
  dom.connectionDetail.textContent = detail || "";
  dom.connectionDot.className = "dot";
  if (connectionState === "connected") {
    dom.connectionDot.classList.add("is-ok");
  } else if (connectionState === "connecting" || connectionState === "closed") {
    dom.connectionDot.classList.add("is-muted");
  } else {
    dom.connectionDot.classList.add("is-error");
  }
}

function showAlert(message) {
  dom.alert.textContent = message;
  dom.alert.hidden = false;
}

function clearAlert() {
  dom.alert.hidden = true;
  dom.alert.textContent = "";
}

function showToast(message, tone = "ok") {
  dom.toast.textContent = message;
  dom.toast.className = `toast toast-${tone}`;
  dom.toast.hidden = false;
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => {
    dom.toast.hidden = true;
  }, 2600);
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) {
    return "--";
  }
  const total = Math.max(0, Math.floor(Number(seconds)));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) {
    return `${days}d ${hours}h`;
  }
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m ${total % 60}s`;
}

function formatTimestamp(value) {
  const date = parseDateValue(value);
  if (!date) {
    return "--";
  }
  return formatDateParts(date);
}

function parseDateValue(value) {
  if (!value) {
    return null;
  }
  if (typeof value === "number") {
    return new Date(value * 1000);
  }
  const text = String(value);
  if (text === "never" || text === "n/a") {
    return null;
  }
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date;
}

function formatDateParts(date, { includeSeconds = false } = {}) {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: includeSeconds ? "2-digit" : undefined,
    hour12: false,
  }).formatToParts(date).reduce((acc, part) => {
    if (part.type !== "literal") {
      acc[part.type] = part.value;
    }
    return acc;
  }, {});
  const base = `${parts.year}年${parts.month}月${parts.day}日 ${parts.hour}时${parts.minute}分`;
  return includeSeconds ? `${base}${parts.second}秒` : base;
}

function normalizeStatus(status) {
  if (status === "ok" || status === "sent") {
    return "ok";
  }
  if (status === "degraded" || status === "warning" || status === "pending" || status === "ready" || status === "rejected") {
    return "warning";
  }
  if (status === "unhealthy" || status === "critical" || status === "error" || status === "failed" || status === "dead") {
    return "critical";
  }
  return "muted";
}

function statusLabel(status) {
  return STATUS_LABELS[String(status || "").toLowerCase()] || status || "未知";
}

function componentLabel(component) {
  return COMPONENT_LABELS[String(component || "")] || component || "未标记模块";
}

function eventLabel(event) {
  const type = typeof event === "string" ? event : event?.type;
  return EVENT_LABELS[String(type || "")] || type || "运行事件";
}

function formatShortTime(value) {
  const date = parseDateValue(value);
  if (!date) {
    return "--";
  }
  return formatDateParts(date);
}

function shortId(value, head = 10, tail = 6) {
  const text = String(value || "");
  if (!text || text.length <= head + tail + 3) {
    return text || "--";
  }
  return `${text.slice(0, head)}...${text.slice(-tail)}`;
}

function describeEventContext(event) {
  const parts = [];
  if (event.component) {
    parts.push(componentLabel(event.component));
  }
  if (event.channel) {
    parts.push(`通道 ${event.channel}`);
  }
  if (event.agent_id) {
    parts.push(`智能体 ${event.agent_id}`);
  }
  if (event.job_id) {
    parts.push(`任务 ${event.job_id}`);
  }
  if (event.delivery_id) {
    parts.push(`投递 ${shortId(event.delivery_id, 8, 4)}`);
  }
  return parts.join(" / ") || "暂无上下文";
}

function describeEventOutcome(event) {
  if (event.error) {
    return `失败原因：${String(event.error).split("\n")[0]}`;
  }
  if (event.message) {
    return event.message;
  }
  if (event.metadata?.failure_reason) {
    return `失败原因：${event.metadata.failure_reason}`;
  }
  return "该节点已记录，无额外说明。";
}

function operatorHint(event) {
  const type = String(event.type || "");
  const component = String(event.component || "");
  const status = normalizeStatus(event.status);
  if (status !== "critical" && status !== "warning") {
    return "无需处理，仅作为链路记录。";
  }
  if (component === "delivery" || type.startsWith("delivery.")) {
    return "优先查看投递队列，确认目标 ID、通道凭证和重试状态。";
  }
  if (component === "tools" || type.startsWith("tool.")) {
    return "检查工具参数、工作目录权限和工具输出长度。";
  }
  if (component === "agent_loop" || type.startsWith("agent.")) {
    return "检查模型配置、会话上下文和最近一次工具调用结果。";
  }
  if (component === "cron" || type.startsWith("cron.")) {
    return "检查 CRON 配置、主动投递目标和任务提示词。";
  }
  if (component === "feishu" || type.startsWith("feishu.")) {
    return "检查飞书事件类型、验签配置、权限和机器人可见范围。";
  }
  return "按链路 ID 过滤最近事件，定位上游和下游节点。";
}

function badge(status) {
  const span = document.createElement("span");
  span.className = `status-badge status-${normalizeStatus(status)}`;
  span.textContent = statusLabel(status);
  return span;
}

function severityWeight(status) {
  const normalized = normalizeStatus(status);
  if (normalized === "critical") {
    return 0;
  }
  if (normalized === "warning") {
    return 1;
  }
  if (normalized === "ok") {
    return 2;
  }
  return 3;
}

function clearNode(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function appendText(parent, tagName, text, className = "") {
  const node = document.createElement(tagName);
  node.textContent = text;
  if (className) {
    node.className = className;
  }
  parent.appendChild(node);
  return node;
}

function appendButton(parent, label, className, onClick) {
  const button = document.createElement("button");
  button.className = className;
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", onClick);
  parent.appendChild(button);
  return button;
}

async function connect() {
  clearAlert();
  const url = dom.wsUrl.value.trim() || defaultWsUrl();
  dom.wsUrl.value = url;
  window.localStorage.setItem("gateway.dashboard.wsUrl", url);
  const client = new JsonRpcClient(url, {
    onStatus: setConnectionStatus,
    onError: (error) => showAlert(error.message),
  });
  state.client = client;
  dom.connectBtn.disabled = true;
  try {
    await client.connect();
    await refreshAll();
  } catch (error) {
    showAlert(error.message);
  } finally {
    dom.connectBtn.disabled = false;
  }
}

async function rpc(method, params = {}) {
  if (!state.client) {
    throw new Error("尚未创建 WebSocket 客户端");
  }
  return state.client.call(method, params);
}

function buildEventQuery(base = {}) {
  const params = { ...base };
  const component = dom.eventComponent.value.trim();
  const status = dom.eventStatus.value.trim();
  const correlationId = dom.eventCorrelation.value.trim();
  if (component) {
    params.component = component;
  }
  if (status) {
    params.status = status;
  }
  if (correlationId) {
    params.correlation_id = correlationId;
  }
  return params;
}

function buildErrorQuery(base = {}) {
  const params = { ...base };
  const component = dom.eventComponent.value.trim();
  const correlationId = dom.eventCorrelation.value.trim();
  if (component) {
    params.component = component;
  }
  if (correlationId) {
    params.correlation_id = correlationId;
  }
  return params;
}

async function refreshAll() {
  clearAlert();
  dom.refreshBtn.disabled = true;
  try {
    const [health, runtime, deliveryStats, deliveryList, cronJobs, events, errors, memories] = await Promise.all([
      rpc("health.check"),
      rpc("runtime.status"),
      rpc("delivery.stats"),
      rpc("delivery.list", {
        state: dom.deliveryState.value,
        limit: 80,
        include_text: dom.includeText.checked,
      }),
      rpc("cron.list"),
      rpc("events.tail", buildEventQuery({ limit: 80 })),
      rpc("errors.recent", buildErrorQuery({ limit: 40 })),
      rpc("memory.recent", { limit: 20 }),
    ]);
    renderSummary(health, runtime, deliveryStats);
    renderIssues(buildIssues(health, runtime, deliveryStats));
    renderHealth(health);
    renderRuntime(runtime);
    renderTraces(events);
    renderEvents(events);
    renderErrors(errors);
    renderMemories(memories);
    renderDelivery(deliveryList);
    renderCron(cronJobs);
  } catch (error) {
    showAlert(error.message);
  } finally {
    dom.refreshBtn.disabled = false;
  }
}

function buildIssues(health, runtime, deliveryStats) {
  const issues = [];
  const checks = Array.isArray(health.checks) ? health.checks : [];
  for (const check of checks) {
    const status = normalizeStatus(check.status);
    if (status === "critical" || status === "warning") {
      issues.push({
        status,
        title: check.name || "unknown check",
        detail: check.message || "",
        target: "#health-panel",
      });
    }
  }

  const delivery = deliveryStats || runtime.delivery || {};
  const failed = Number(delivery.failed || 0);
  const pending = Number(delivery.pending || 0);
  if (failed > 0) {
    issues.push({
      status: "warning",
      title: `${failed} 条投递失败`,
      detail: "进入投递队列查看错误原因，永久失败建议丢弃，临时失败可重试。",
      target: "#delivery-panel",
    });
  }
  if (pending > 0) {
    issues.push({
      status: "warning",
      title: `${pending} 条投递待处理`,
      detail: `${delivery.retry_ready || 0} 条可立即重试或 flush。`,
      target: "#delivery-panel",
    });
  }

  if (Number(runtime.profiles?.available || 0) <= 0) {
    issues.push({
      status: "critical",
      title: "没有可用模型 Profile",
      detail: "检查 ANTHROPIC_API_KEY、ANTHROPIC_BASE_URL 或 profile cooldown。",
      target: "#runtime-panel",
    });
  }
  if (Number(runtime.channels?.active || 0) <= 0) {
    issues.push({
      status: "warning",
      title: "没有活跃通道",
      detail: "检查 config/channels.json 和通道密钥环境变量。",
      target: "#runtime-panel",
    });
  }
  return issues.sort((left, right) => severityWeight(left.status) - severityWeight(right.status));
}

function renderIssues(issues) {
  clearNode(dom.issueList);
  dom.issueList.className = issues.length ? "issue-list" : "issue-list empty";
  dom.issueSummary.textContent = issues.length ? `${issues.length} 个待处理项` : "当前无异常";
  if (!issues.length) {
    dom.issueList.textContent = "当前健康检查、投递队列和运行态没有发现需要优先处理的问题。";
    return;
  }
  for (const issue of issues) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `issue-item issue-${issue.status}`;
    item.addEventListener("click", () => scrollToTarget(issue.target));
    item.appendChild(badge(issue.status));
    const body = document.createElement("div");
    appendText(body, "div", issue.title, "item-title");
    appendText(body, "div", issue.detail, "item-meta");
    item.appendChild(body);
    dom.issueList.appendChild(item);
  }
}

function renderSummary(health, runtime, deliveryStats) {
  const server = runtime.server || health.server || {};
  const channels = runtime.channels || {};
  const profiles = runtime.profiles || {};
  const cron = runtime.cron || {};
  const delivery = deliveryStats || runtime.delivery || {};

  dom.metricHealth.textContent = health.status || "--";
  dom.metricHealthDetail.textContent = `${health.summary?.critical || 0} 个严重 / ${health.summary?.warning || 0} 个注意`;
  const healthCard = dom.metricHealth.closest(".metric-health");
  healthCard.classList.remove("is-ok", "is-warning", "is-critical");
  healthCard.classList.add(`is-${normalizeStatus(health.status)}`);

  dom.metricUptime.textContent = formatDuration(server.uptime_seconds);
  dom.metricServer.textContent = server.running ? "服务正在运行" : "服务未运行";
  dom.metricDelivery.textContent = `${delivery.pending ?? "--"} / ${delivery.failed ?? "--"}`;
  dom.metricDeliveryDetail.textContent = `${delivery.retry_ready ?? 0} 条可立即重试`;
  dom.metricChannels.textContent = `${channels.active ?? "--"} / ${channels.count ?? "--"}`;
  dom.metricChannelsDetail.textContent = "活跃通道 / 已配置通道";
  dom.metricProfiles.textContent = `${profiles.available ?? "--"} / ${profiles.count ?? "--"}`;
  dom.metricProfilesDetail.textContent = "可用 Profile / 已配置 Profile";
  dom.metricCron.textContent = `${cron.enabled ?? "--"} / ${cron.count ?? "--"}`;
  dom.metricCronDetail.textContent = `${cron.errored ?? 0} 个任务有错误`;
}

function renderHealth(health) {
  clearNode(dom.healthList);
  const checks = (Array.isArray(health.checks) ? health.checks : [])
    .slice()
    .sort((left, right) => severityWeight(left.status) - severityWeight(right.status));
  dom.healthList.className = checks.length ? "check-list" : "check-list empty";
  dom.healthSummary.textContent = `${statusLabel(health.status)} | ${health.summary?.ok || 0} 项正常`;
  if (!checks.length) {
    dom.healthList.textContent = "没有健康检查数据";
    return;
  }
  for (const check of checks) {
    const item = document.createElement("div");
    item.className = `check-item check-${normalizeStatus(check.status)}`;
    item.appendChild(badge(check.status));
    const body = document.createElement("div");
    appendText(body, "div", check.name || "unknown", "item-title");
    appendText(body, "div", check.message || "", "item-meta");
    item.appendChild(body);
    dom.healthList.appendChild(item);
  }
}

function renderRuntime(runtime) {
  clearNode(dom.runtimeList);
  dom.runtimeList.className = "runtime-section-list";
  dom.runtimeUpdated.textContent = formatTimestamp(Date.now() / 1000);

  const channels = runtime.channels || {};
  const profiles = runtime.profiles || {};
  const features = runtime.features || {};
  const proactive = features.proactive_target || {};
  const paths = runtime.paths || {};
  const sections = [
    {
      icon: "AG",
      title: "智能体",
      value: `${runtime.agents?.count ?? 0} 个`,
      status: Number(runtime.agents?.count || 0) > 0 ? "ok" : "critical",
      summary: `${runtime.bindings?.count ?? 0} 条路由绑定`,
      chips: [
        `智能体 ${runtime.agents?.count ?? 0}`,
        `路由 ${runtime.bindings?.count ?? 0}`,
      ],
      rows: [
        ["智能体 ID", (runtime.agents?.ids || []).join(", ") || "无"],
        ["路由绑定", `${runtime.bindings?.count ?? 0} 条`],
      ],
    },
    {
      icon: "CH",
      title: "通道",
      value: `${channels.active ?? 0}/${channels.count ?? 0}`,
      status: Number(channels.active || 0) > 0 ? "ok" : "warning",
      summary: "活跃通道 / 已配置通道",
      chips: [
        `活跃 ${channels.active ?? 0}`,
        `总数 ${channels.count ?? 0}`,
      ],
      rows: (channels.items || []).map((row) => [
        `${row.channel}:${row.account_id}`,
        `${row.active ? "活跃" : "未活跃"} | ${row.enabled ? "已启用" : "已停用"} | 密钥${row.has_token ? "已配置" : "缺失"}`,
      ]),
    },
    {
      icon: "AI",
      title: "模型 Profile",
      value: `${profiles.available ?? 0}/${profiles.count ?? 0}`,
      status: Number(profiles.available || 0) > 0 ? "ok" : "critical",
      summary: "可用模型 Profile / 总数",
      chips: [
        `可用 ${profiles.available ?? 0}`,
        `总数 ${profiles.count ?? 0}`,
      ],
      rows: (profiles.items || []).map((row) => [
        row.name || "profile",
        `密钥${row.has_key ? "已配置" : "缺失"} | 冷却剩余 ${row.cooldown_remaining || 0}s`,
      ]),
    },
    {
      icon: "FX",
      title: "能力开关",
      value: features.web_search_enabled ? "Search On" : "Search Off",
      status: features.web_search_enabled && !features.web_search_has_key ? "warning" : "ok",
      summary: proactive.peer_id_configured ? "主动投递目标已配置" : "主动投递目标未完整配置",
      chips: [
        features.web_search_enabled ? "联网搜索已开" : "联网搜索关闭",
        proactive.peer_id_configured ? "主动投递可用" : "主动投递缺配置",
      ],
      rows: [
        [
          "联网搜索",
          `${features.web_search_provider || "--"} | 密钥${features.web_search_has_key ? "已配置" : "缺失"}`,
        ],
        [
          "主动投递",
          `${proactive.channel || "--"}:${proactive.account_id || "--"} -> ${proactive.agent_id || "--"} | 目标${proactive.peer_id_configured ? "已配置" : "缺失"}`,
        ],
      ],
    },
    {
      icon: "FS",
      title: "本地目录",
      value: paths.workspace_exists && paths.data_dir_exists && paths.config_dir_exists ? "OK" : "Check",
      status: paths.workspace_exists && paths.data_dir_exists && paths.config_dir_exists ? "ok" : "critical",
      summary: "workspace / data / config",
      chips: [
        paths.workspace_exists ? "workspace 正常" : "workspace 缺失",
        paths.data_dir_exists ? "data 正常" : "data 缺失",
        paths.config_dir_exists ? "config 正常" : "config 缺失",
      ],
      rows: [
        ["workspace", paths.workspace_root || "--"],
        ["data", paths.data_dir || "--"],
        ["config", paths.config_dir || "--"],
      ],
    },
  ];

  for (const section of sections) {
    const card = document.createElement("div");
    card.className = `runtime-section runtime-${normalizeStatus(section.status)}`;

    const head = document.createElement("div");
    head.className = "runtime-section-head";
    appendText(head, "span", section.icon, "runtime-icon");
    const title = document.createElement("div");
    title.className = "runtime-section-title";
    appendText(title, "strong", section.title);
    appendText(title, "small", section.summary);
    head.appendChild(title);
    appendText(head, "span", section.value, "runtime-value");
    card.appendChild(head);

    const chipRow = document.createElement("div");
    chipRow.className = "runtime-chips";
    for (const chip of section.chips) {
      appendText(chipRow, "span", chip);
    }
    card.appendChild(chipRow);

    const details = document.createElement("details");
    details.className = "runtime-details";
    const summary = document.createElement("summary");
    summary.textContent = "查看技术详情";
    details.appendChild(summary);
    const rows = section.rows.length ? section.rows : [["empty", "无数据"]];
    for (const [label, value] of rows) {
      const row = document.createElement("div");
      row.className = "kv-row";
      appendText(row, "span", label);
      appendText(row, "code", value || "--");
      details.appendChild(row);
    }
    card.appendChild(details);
    dom.runtimeList.appendChild(card);
  }
}

function classifyDeliveryError(item) {
  const error = String(item.last_error || "").toLowerCase();
  if (!error) {
    return {
      code: "none",
      label: "无错误",
      severity: "ok",
      suggestion: "该消息暂无错误记录。",
    };
  }
  if (
    error.includes("99992351")
    || error.includes("invalid ids")
    || (error.includes("not a valid") && error.includes("open_id"))
  ) {
    return {
      code: "invalid_open_id",
      label: "无效 open_id",
      severity: "critical",
      suggestion: "目标飞书 open_id 不存在或不是当前应用可达用户，建议丢弃该历史消息，并修正来源配置。",
    };
  }
  if (error.includes("channel unavailable")) {
    return {
      code: "channel_unavailable",
      label: "通道不可用",
      severity: "warning",
      suggestion: "检查 channels 配置、account_id 和通道是否启用，然后重试。",
    };
  }
  if (error.includes("tenant token") || error.includes("unauthorized")) {
    return {
      code: "auth_failed",
      label: "认证失败",
      severity: "critical",
      suggestion: "检查飞书 App ID、App Secret、机器人权限和环境变量。",
    };
  }
  if (error.includes("rate") || error.includes("too many")) {
    return {
      code: "rate_limited",
      label: "限流",
      severity: "warning",
      suggestion: "等待限流恢复后重试，必要时降低主动任务或批量投递频率。",
    };
  }
  return {
    code: "temporary_failure",
    label: "临时失败",
    severity: "warning",
    suggestion: "可先重试；若持续失败，再检查通道日志和目标配置。",
  };
}

function renderDelivery(deliveryList) {
  clearNode(dom.deliveryTable);
  const items = Array.isArray(deliveryList.items) ? deliveryList.items : [];
  if (!items.length) {
    dom.deliveryTable.className = "delivery-cards empty";
    dom.deliveryTable.textContent = `当前“${statusLabel(deliveryList.state || dom.deliveryState.value)}”队列为空`;
    return;
  }
  dom.deliveryTable.className = "delivery-cards";
  for (const item of items) {
    const classification = classifyDeliveryError(item);
    const card = document.createElement("article");
    card.className = `delivery-card delivery-${normalizeStatus(item.state || classification.severity)}`;

    const head = document.createElement("div");
    head.className = "delivery-card-head";
    head.appendChild(badge(item.state || classification.severity));
    const title = document.createElement("div");
    appendText(title, "strong", `${item.channel || "未知通道"} -> ${shortId(item.to, 14, 6)}`, "delivery-title");
    appendText(
      title,
      "small",
      `投递 ${shortId(item.id, 10, 5)} · 已重试 ${item.retry_count ?? 0} 次 · ${
        item.retry_ready ? "现在可重试" : `下次重试 ${item.next_retry_in_seconds ?? "--"} 秒后`
      }`,
      "delivery-meta",
    );
    head.appendChild(title);

    const actions = document.createElement("div");
    actions.className = "row-actions";
    appendButton(actions, "详情", "button button-small", () => showDeliveryDetail(item));
    appendButton(actions, "复制 ID", "button button-small", () => copyText(item.id || ""));
    appendButton(actions, "重试", "button button-small", () => retryDelivery(item.id, classification));
    appendButton(actions, "丢弃", "button button-small button-danger", () => discardDelivery(item.id, item.state, classification));
    head.appendChild(actions);
    card.appendChild(head);

    const reason = document.createElement("div");
    reason.className = "delivery-reason";
    reason.appendChild(badge(classification.severity));
    const reasonText = document.createElement("div");
    appendText(reasonText, "strong", classification.label);
    appendText(reasonText, "small", classification.suggestion);
    if (item.last_error) {
      appendText(reasonText, "span", String(item.last_error).split("\n")[0], "preview");
    }
    reason.appendChild(reasonText);
    card.appendChild(reason);

    const text = item.text || item.text_preview || "";
    if (text) {
      appendText(card, "p", text, "delivery-preview");
    }
    dom.deliveryTable.appendChild(card);
  }
}

function renderCron(cronJobs) {
  clearNode(dom.cronList);
  const jobs = Array.isArray(cronJobs) ? cronJobs : [];
  dom.cronList.className = jobs.length ? "cron-list" : "cron-list empty";
  dom.cronSummary.textContent = `${jobs.filter((job) => job.enabled).length}/${jobs.length} 已启用`;
  if (!jobs.length) {
    dom.cronList.textContent = "没有 Cron 任务";
    return;
  }
  for (const group of groupCronJobs(jobs)) {
    const section = document.createElement("section");
    section.className = "cron-group";
    const head = document.createElement("div");
    head.className = "cron-group-head";
    appendText(head, "strong", group.title);
    appendText(head, "span", `${group.jobs.filter((job) => job.enabled).length}/${group.jobs.length} 已启用`);
    section.appendChild(head);
    for (const job of group.jobs) {
      const item = document.createElement("div");
      item.className = `cron-item cron-${normalizeStatus(job.enabled ? "ok" : "muted")}`;
      const body = document.createElement("div");
      appendText(body, "div", job.name || job.config_id || job.id || "未命名任务", "item-title");
      appendText(
        body,
        "div",
        [
          job.enabled ? "已启用" : "已停用",
          `智能体 ${job.agent_id || "--"}`,
          `任务 ${job.config_id || job.id || "--"}`,
          `错误 ${job.errors ?? 0} 次`,
        ].join(" · "),
        "item-meta",
      );
      appendText(
        body,
        "div",
        `下次运行：${formatTimestamp(job.next_run)} · 上次运行：${formatTimestamp(job.last_run)}`,
        "item-meta",
      );
      if (job.source_file) {
        appendText(body, "div", `配置来源：${job.source_file}`, "item-meta mono");
      }
      const trigger = document.createElement("button");
      trigger.className = "button button-small";
      trigger.type = "button";
      trigger.textContent = "立即触发";
      trigger.disabled = !job.id;
      trigger.addEventListener("click", () => triggerCron(job.id, job.name || job.id));
      item.append(body, trigger);
      section.appendChild(item);
    }
    dom.cronList.appendChild(section);
  }
}

function groupCronJobs(jobs) {
  const groups = new Map();
  for (const job of jobs) {
    const scope = job.scope || "global";
    if (!groups.has(scope)) {
      groups.set(scope, []);
    }
    groups.get(scope).push(job);
  }
  return [...groups.entries()]
    .sort(([left], [right]) => {
      if (left === "global") {
        return -1;
      }
      if (right === "global") {
        return 1;
      }
      return left.localeCompare(right);
    })
    .map(([scope, rows]) => ({
      title: scope === "global" ? "全局任务" : `智能体 ${scope}`,
      jobs: rows.slice().sort((left, right) => String(left.config_id || left.id).localeCompare(String(right.config_id || right.id))),
    }));
}

function renderEvents(payload) {
  renderEventList({
    container: dom.eventsList,
    summary: dom.eventsSummary,
    payload,
    emptyText: "暂无运行事件。发送一条消息、触发 Cron 或 flush 投递队列后会出现链路事件。",
    summaryLabel: "条事件",
    collapsible: true,
    expanded: state.eventsExpanded,
    collapsedLimit: 8,
    onToggle: () => {
      state.eventsExpanded = !state.eventsExpanded;
      renderEvents(payload);
    },
  });
}

function renderTraces(payload) {
  clearNode(dom.tracesList);
  const events = Array.isArray(payload?.items) ? payload.items.slice() : [];
  const traces = buildTraces(events);
  dom.tracesSummary.textContent = `${traces.length} 条链路`;
  dom.tracesList.className = traces.length ? "trace-list" : "trace-list empty";
  if (!traces.length) {
    dom.tracesList.textContent = "暂无可聚合链路。事件需要包含 correlation_id 才会出现在这里。";
    return;
  }
  for (const trace of traces) {
    const details = document.createElement("details");
    details.className = `trace-item trace-${trace.severity}`;
    const summary = document.createElement("summary");
    summary.className = "trace-summary";
    summary.appendChild(badge(trace.severity === "critical" ? "error" : trace.severity));
    const title = document.createElement("div");
    appendText(title, "strong", trace.title);
    appendText(
      title,
      "small",
      `${trace.events.length} 个节点 · ${trace.componentLabels.join("、") || "未标记模块"} · ${formatShortTime(trace.start)} - ${formatShortTime(trace.end)} · 链路 ${shortId(trace.correlationId, 12, 6)}`,
    );
    summary.appendChild(title);
    appendText(summary, "span", eventLabel(trace.lastType), "trace-last-type");
    details.appendChild(summary);
    if (trace.lastError) {
      appendText(details, "pre", `最近错误：${trace.lastError}`, "event-error");
    }
    const timeline = document.createElement("div");
    timeline.className = "trace-timeline";
    for (const event of trace.events) {
      const row = document.createElement("div");
      row.className = `trace-event trace-event-${normalizeStatus(event.status)}`;
      appendText(row, "span", formatShortTime(event.timestamp), "event-time");
      appendText(row, "strong", eventLabel(event));
      appendText(row, "small", `${statusLabel(event.status)} · ${describeEventContext(event)}`);
      timeline.appendChild(row);
    }
    details.appendChild(timeline);
    dom.tracesList.appendChild(details);
  }
}

function buildTraces(events) {
  const groups = new Map();
  for (const event of events) {
    const id = String(event.correlation_id || "").trim();
    if (!id) {
      continue;
    }
    if (!groups.has(id)) {
      groups.set(id, []);
    }
    groups.get(id).push(event);
  }
  const traces = [];
  for (const [correlationId, rows] of groups.entries()) {
    const sorted = rows.slice().sort((left, right) => Number(left.timestamp || 0) - Number(right.timestamp || 0));
    const last = sorted[sorted.length - 1] || {};
    const errorEvents = sorted.filter((event) => event.error || normalizeStatus(event.status) === "critical");
    const hasWarning = sorted.some((event) => normalizeStatus(event.status) === "warning");
    traces.push({
      correlationId,
      events: sorted,
      start: sorted[0]?.timestamp,
      end: last.timestamp,
      lastType: last.type || "",
      lastError: errorEvents[errorEvents.length - 1]?.error || "",
      title: `${eventLabel(sorted[0])} -> ${eventLabel(last)}`,
      severity: errorEvents.length ? "critical" : hasWarning ? "warning" : "ok",
      components: [...new Set(sorted.map((event) => event.component).filter(Boolean))],
      componentLabels: [...new Set(sorted.map((event) => componentLabel(event.component)).filter(Boolean))],
    });
  }
  return traces.sort((left, right) => {
    const bySeverity = severityWeight(left.severity) - severityWeight(right.severity);
    if (bySeverity !== 0) {
      return bySeverity;
    }
    return Number(right.end || 0) - Number(left.end || 0);
  }).slice(0, 24);
}

function renderErrors(payload) {
  renderEventList({
    container: dom.errorsList,
    summary: dom.errorsSummary,
    payload,
    emptyText: "最近没有错误、失败或拒绝事件。",
    summaryLabel: "条错误",
  });
}

function renderMemories(payload) {
  clearNode(dom.memoryList);
  const items = Array.isArray(payload?.items) ? payload.items : [];
  dom.memorySummary.textContent = `${items.length} 条记录`;
  dom.memoryList.className = items.length ? "memory-list" : "memory-list empty";
  if (!items.length) {
    dom.memoryList.textContent = "最近没有记忆写入。";
    return;
  }
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "memory-item";
    const head = document.createElement("div");
    head.className = "memory-head";
    const title = document.createElement("div");
    appendText(title, "strong", item.category ? `类别：${item.category}` : "未分类记忆");
    appendText(title, "small", `来源文件：${item.file || "--"}`);
    head.appendChild(title);
    appendText(head, "span", formatTimestamp(item.ts), "event-time");
    card.appendChild(head);
    appendText(card, "p", item.content || "", "memory-content");
    dom.memoryList.appendChild(card);
  }
}

function renderEventList({
  container,
  summary,
  payload,
  emptyText,
  summaryLabel,
  collapsible = false,
  expanded = true,
  collapsedLimit = 8,
  onToggle = null,
}) {
  clearNode(container);
  const items = Array.isArray(payload?.items) ? payload.items.slice().reverse() : [];
  const visibleItems = collapsible && !expanded ? items.slice(0, collapsedLimit) : items;
  summary.textContent = collapsible && items.length > collapsedLimit
    ? `${items.length} ${summaryLabel}，当前显示 ${visibleItems.length} 条`
    : `${items.length} ${summaryLabel}`;
  container.className = items.length ? "event-list" : "event-list empty";
  if (!items.length) {
    container.textContent = emptyText;
    return;
  }
  for (const event of visibleItems) {
    const item = document.createElement("div");
    item.className = `event-item event-${normalizeStatus(event.status)}`;
    const head = document.createElement("div");
    head.className = "event-head";
    head.appendChild(badge(event.status || "ok"));
    const title = document.createElement("div");
    appendText(title, "strong", eventLabel(event));
    appendText(title, "small", describeEventContext(event), "event-context");
    head.appendChild(title);
    appendText(head, "span", formatShortTime(event.timestamp), "event-time");
    item.appendChild(head);

    appendText(item, "div", describeEventOutcome(event), "item-title");
    appendText(item, "div", operatorHint(event), "item-meta");
    if (event.error) {
      appendText(item, "pre", event.error, "event-error");
    }
    const details = document.createElement("details");
    details.className = "runtime-details";
    const detailsSummary = document.createElement("summary");
    detailsSummary.textContent = "查看技术详情";
    details.appendChild(detailsSummary);
    const rows = [
      ["correlation_id", event.correlation_id],
      ["component", componentLabel(event.component)],
      ["agent_id", event.agent_id],
      ["session_key", event.session_key],
      ["channel", event.channel],
      ["account_id", event.account_id],
      ["peer_id", event.peer_id],
      ["delivery_id", event.delivery_id],
      ["job_id", event.job_id],
      ["metadata", JSON.stringify(event.metadata || {}, null, 2)],
    ].filter(([, value]) => value !== undefined && value !== null && String(value) !== "");
    for (const [label, value] of rows) {
      const row = document.createElement("div");
      row.className = "kv-row";
      appendText(row, "span", DETAIL_LABELS[label] || label);
      appendText(row, "code", value || "--");
      details.appendChild(row);
    }
    item.appendChild(details);
    container.appendChild(item);
  }
  if (collapsible && items.length > collapsedLimit) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "event-collapse-toggle";
    more.textContent = expanded
      ? "收起最近事件"
      : `展开全部 ${items.length} 条事件`;
    more.addEventListener("click", () => onToggle?.());
    container.appendChild(more);
  }
}

async function retryDelivery(deliveryId, classification = null) {
  if (!deliveryId) {
    return;
  }
  const confirmed = confirmAction(
    `确认立即重试投递 ${deliveryId}？`,
    classification?.suggestion || "该操作会把消息设为可立即投递。",
  );
  if (!confirmed) {
    return;
  }
  try {
    await rpc("delivery.retry", { delivery_id: deliveryId });
    showToast(`已请求重试 ${deliveryId}`);
    await refreshAll();
  } catch (error) {
    showAlert(error.message);
  }
}

async function discardDelivery(deliveryId, deliveryState, classification = null) {
  if (!deliveryId) {
    return;
  }
  const confirmed = confirmAction(
    `确认丢弃投递消息 ${deliveryId}？`,
    classification?.suggestion || "丢弃后该消息不会再自动投递。",
  );
  if (!confirmed) {
    return;
  }
  try {
    await rpc("delivery.discard", { delivery_id: deliveryId, state: deliveryState || "any" });
    showToast(`已丢弃 ${deliveryId}`, "warning");
    await refreshAll();
  } catch (error) {
    showAlert(error.message);
  }
}

async function flushDelivery() {
  const confirmed = confirmAction(
    "确认立即 flush 投递队列？",
    "该操作会立即尝试发送所有已到重试时间的 pending 消息。",
  );
  if (!confirmed) {
    return;
  }
  try {
    await rpc("delivery.flush", { rounds: 3 });
    showToast("投递队列 flush 已完成");
    await refreshAll();
  } catch (error) {
    showAlert(error.message);
  }
}

async function triggerCron(jobId, jobName = "") {
  if (!jobId) {
    return;
  }
  const confirmed = confirmAction(
    `确认立即触发 Cron 任务 ${jobId}？`,
    jobName ? `任务名称：${jobName}` : "该操作可能触发模型调用和主动投递。",
  );
  if (!confirmed) {
    return;
  }
  try {
    await rpc("cron.trigger", { job_id: jobId });
    showToast(`已触发 Cron：${jobId}`);
    await refreshAll();
  } catch (error) {
    showAlert(error.message);
  }
}

function confirmAction(title, detail) {
  return window.confirm(`${title}\n\n${detail}`);
}

async function copyText(text) {
  if (!text) {
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    showToast("已复制到剪贴板");
  } catch (error) {
    showAlert(`复制失败：${error.message}`);
  }
}

function showDeliveryDetail(item) {
  clearNode(dom.deliveryDetailBody);
  const classification = classifyDeliveryError(item);
  const rows = [
    ["投递 ID", item.id || ""],
    ["当前状态", statusLabel(item.state)],
    ["通道", item.channel || ""],
    ["目标", item.to || ""],
    ["问题类型", classification.label],
    ["处理建议", classification.suggestion],
    ["重试次数", String(item.retry_count ?? 0)],
    ["是否可立即重试", item.retry_ready ? "是" : "否"],
    ["下次重试时间", formatTimestamp(item.next_retry_at)],
    ["入队时间", formatTimestamp(item.enqueued_at)],
    ["最近错误", item.last_error || ""],
    ["正文", item.text || item.text_preview || ""],
    ["技术元数据", JSON.stringify(item.metadata || {}, null, 2)],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "detail-row";
    appendText(row, "span", label);
    appendText(row, "pre", value || "--");
    dom.deliveryDetailBody.appendChild(row);
  }
  dom.deliveryDetail.classList.add("is-open");
  dom.deliveryDetail.setAttribute("aria-hidden", "false");
}

function closeDeliveryDetail() {
  dom.deliveryDetail.classList.remove("is-open");
  dom.deliveryDetail.setAttribute("aria-hidden", "true");
}

function scrollToTarget(selector) {
  const target = document.querySelector(selector);
  if (target) {
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function restartAutoRefresh() {
  if (state.autoRefreshTimer) {
    window.clearInterval(state.autoRefreshTimer);
    state.autoRefreshTimer = null;
  }
  if (dom.autoRefresh.checked) {
    state.autoRefreshTimer = window.setInterval(() => {
      if (state.client?.socket?.readyState === WebSocket.OPEN) {
        refreshAll();
      }
    }, state.refreshIntervalSeconds * 1000);
  }
}

async function bootstrap() {
  dom.wsUrl.value = defaultWsUrl();
  await loadDashboardConfig();
  dom.connectBtn.addEventListener("click", connect);
  dom.refreshBtn.addEventListener("click", refreshAll);
  dom.deliveryState.addEventListener("change", refreshAll);
  dom.includeText.addEventListener("change", refreshAll);
  dom.eventComponent.addEventListener("change", refreshAll);
  dom.eventStatus.addEventListener("change", refreshAll);
  dom.eventCorrelation.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      refreshAll();
    }
  });
  dom.deliveryFlushBtn.addEventListener("click", flushDelivery);
  dom.autoRefresh.addEventListener("change", restartAutoRefresh);
  dom.deliveryDetailClose.addEventListener("click", closeDeliveryDetail);
  dom.deliveryDetail.addEventListener("click", (event) => {
    if (event.target?.hasAttribute?.("data-close-detail")) {
      closeDeliveryDetail();
    }
  });
  document.querySelectorAll("[data-jump]").forEach((node) => {
    node.addEventListener("click", () => scrollToTarget(node.getAttribute("data-jump")));
  });

  restartAutoRefresh();
  window.setTimeout(connect, 250);
}

bootstrap();
