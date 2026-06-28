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
  panelExpanded: {},
};

const DEFAULT_PANEL_LIMIT = 6;

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
  metricsSummary: $("#metrics-summary"),
  metricsHighlights: $("#metrics-highlights"),
  metricsTrends: $("#metrics-trends"),
  alertsSummary: $("#alerts-summary"),
  alertsActive: $("#alerts-active"),
  alertsHistorySummary: $("#alerts-history-summary"),
  alertsHistory: $("#alerts-history"),
  healthSummary: $("#health-summary"),
  healthList: $("#health-list"),
  inboundSummary: $("#inbound-summary"),
  inboundList: $("#inbound-list"),
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
  tasksSummary: $("#tasks-summary"),
  tasksList: $("#tasks-list"),
  deliveryState: $("#delivery-state"),
  includeText: $("#include-text"),
  deliveryFlushBtn: $("#delivery-flush-btn"),
  deliveryRepublishBtn: $("#delivery-republish-btn"),
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
  running: "执行中",
  retrying: "等待重试",
  done: "已完成",
  cancelled: "已取消",
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
  timestamp: "发生时间",
  created_at: "创建时间",
  updated_at: "更新时间",
  started_at: "开始时间",
  finished_at: "完成时间",
  enqueued_at: "入队时间",
  next_retry_at: "下次重试时间",
  run_at: "运行时间",
  received_at: "接收时间",
  expires_at: "过期时间",
};

const TIME_FIELD_NAMES = new Set([
  "timestamp",
  "created_at",
  "updated_at",
  "started_at",
  "finished_at",
  "enqueued_at",
  "next_retry_at",
  "last_message_at",
  "last_triggered_at",
  "last_recovered_at",
  "last_evaluated_at",
  "last_notified_at",
  "last_good_at",
  "run_at",
  "received_at",
  "seen_at",
  "collected_at",
  "expires_at",
]);

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
  if (typeof value === "string" && isFormattedTimeText(value)) {
    return value;
  }
  const date = parseDateValue(value);
  if (!date) {
    return "--";
  }
  return formatDateParts(date);
}

function formatDisplayValue(label, value) {
  if (value === undefined || value === null || value === "") {
    return "--";
  }
  const key = String(label || "");
  if (isTimeFieldName(key)) {
    return formatTimestamp(value);
  }
  if (typeof value === "object") {
    return JSON.stringify(formatNestedDisplayValue(value), null, 2);
  }
  return String(value);
}

function parseDateValue(value) {
  if (!value) {
    return null;
  }
  if (typeof value === "number") {
    return new Date(value > 100000000000 ? value : value * 1000);
  }
  const text = String(value).trim();
  if (text === "never" || text === "n/a") {
    return null;
  }
  if (/^\d+(\.\d+)?$/.test(text)) {
    const numeric = Number(text);
    return new Date(numeric > 100000000000 ? numeric : numeric * 1000);
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

function isFormattedTimeText(value) {
  return /^\d{4}年\d{2}月\d{2}日 \d{2}时\d{2}分(\d{2}秒)?$/.test(String(value || "").trim());
}

function isTimeFieldName(name) {
  const key = String(name || "").toLowerCase();
  return TIME_FIELD_NAMES.has(key) || key.endsWith("_time") || key.endsWith("_at");
}

function formatNestedDisplayValue(value, key = "") {
  if (Array.isArray(value)) {
    return value.map((item) => formatNestedDisplayValue(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([entryKey, entryValue]) => [
        entryKey,
        formatNestedDisplayValue(entryValue, entryKey),
      ]),
    );
  }
  if (isTimeFieldName(key)) {
    const formatted = formatTimestamp(value);
    return formatted === "--" ? value : formatted;
  }
  return value;
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

function isPanelExpanded(key) {
  return Boolean(state.panelExpanded[key]);
}

function togglePanelExpanded(key) {
  state.panelExpanded[key] = !state.panelExpanded[key];
}

function slicePanelItems(items, key, limit = DEFAULT_PANEL_LIMIT) {
  if (!Array.isArray(items) || items.length <= limit || isPanelExpanded(key)) {
    return items;
  }
  return items.slice(0, limit);
}

function appendCollapseToggle(container, key, total, renderFn, limit = DEFAULT_PANEL_LIMIT) {
  if (!Number.isFinite(total) || total <= limit) {
    return;
  }
  const button = document.createElement("button");
  button.type = "button";
  button.className = "event-collapse-toggle";
  button.textContent = isPanelExpanded(key)
    ? "收起"
    : `展开剩余 ${total - limit} 条`;
  button.addEventListener("click", () => {
    togglePanelExpanded(key);
    renderFn();
  });
  container.appendChild(button);
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
    const [health, runtime, deliveryStats, deliveryList, cronJobs, events, errors, memories, tasks, metricsSummary, metricsTail, alertsActive, alertsHistory] = await Promise.all([
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
      rpc("tasks.list", { status: "all", limit: 40 }),
      rpc("metrics.summary", { limit: 60 }),
      rpc("metrics.tail", { limit: 24 }),
      rpc("alerts.active"),
      rpc("alerts.history", { limit: 20 }),
    ]);
    renderSummary(health, runtime, deliveryStats, metricsSummary);
    renderIssues(buildIssues(health, runtime, deliveryStats, metricsSummary));
    renderHealth(health);
    renderRuntime(runtime);
    renderInbound(runtime.inbound || {});
    renderMetrics(metricsSummary, metricsTail);
    renderAlerts(alertsActive, alertsHistory);
    renderTraces(events);
    renderEvents(events);
    renderErrors(errors);
    renderMemories(memories);
    renderTasks(tasks);
    renderDelivery(deliveryList);
    renderCron(cronJobs);
  } catch (error) {
    showAlert(error.message);
  } finally {
    dom.refreshBtn.disabled = false;
  }
}

function buildIssues(health, runtime, deliveryStats, metricsSummary = {}) {
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
  const retrying = Number(delivery.retrying || 0);
  const dlq = Number(delivery.broker?.dead_letter_messages || 0);
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
  if (retrying > 0 || dlq > 0) {
    issues.push({
      status: dlq > 0 ? "warning" : "info",
      title: `${retrying} 条等待重试，DLQ ${dlq} 条`,
      detail: "可使用“重建队列”从事实状态重新发布 RabbitMQ 引用。",
      target: "#delivery-panel",
    });
  }

  const metricEvents = metricsSummary.events || {};
  if (Number(metricEvents.max_errors_5m || 0) > 0) {
    issues.push({
      status: Number(metricEvents.max_errors_5m || 0) >= 3 ? "critical" : "warning",
      title: `最近窗口出现 ${metricEvents.max_errors_5m} 次错误`,
      detail: "查看指标趋势和最近错误，确认异常来自模型、工具、投递还是飞书接入。",
      target: "#metrics-panel",
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
  const visibleIssues = slicePanelItems(issues, "issues");
  for (const issue of visibleIssues) {
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
  appendCollapseToggle(dom.issueList, "issues", issues.length, () => renderIssues(issues));
}

function renderSummary(health, runtime, deliveryStats, metricsSummary = {}) {
  const server = runtime.server || health.server || {};
  const channels = runtime.channels || {};
  const profiles = runtime.profiles || {};
  const cron = runtime.cron || {};
  const delivery = deliveryStats || runtime.delivery || {};
  const metricEvents = metricsSummary.events || {};
  const maxErrors = Number(metricEvents.max_errors_5m || 0);
  const maxRejected = Number(metricEvents.max_rejected_5m || 0);

  dom.metricHealth.textContent = health.status || "--";
  dom.metricHealthDetail.textContent = maxErrors > 0
    ? `最近窗口 ${maxErrors} 次错误 / ${maxRejected} 次拒绝`
    : `${health.summary?.critical || 0} 个严重 / ${health.summary?.warning || 0} 个注意`;
  const healthCard = dom.metricHealth.closest(".metric-health");
  healthCard.classList.remove("is-ok", "is-warning", "is-critical");
  healthCard.classList.add(`is-${normalizeStatus(health.status)}`);

  dom.metricUptime.textContent = formatDuration(server.uptime_seconds);
  dom.metricServer.textContent = server.running ? "服务正在运行" : "服务未运行";
  dom.metricDelivery.textContent = `${delivery.pending ?? "--"} / ${delivery.retrying ?? 0} / ${delivery.failed ?? "--"}`;
  dom.metricDeliveryDetail.textContent = `待投递 / 等待重试 / 失败，DLQ ${delivery.broker?.dead_letter_messages ?? 0} 条`;
  dom.metricChannels.textContent = `${channels.active ?? "--"} / ${channels.count ?? "--"}`;
  dom.metricChannelsDetail.textContent = "活跃通道 / 已配置通道";
  dom.metricProfiles.textContent = `${profiles.available ?? "--"} / ${profiles.count ?? "--"}`;
  dom.metricProfilesDetail.textContent = "可用 Profile / 已配置 Profile";
  dom.metricCron.textContent = `${cron.enabled ?? "--"} / ${cron.count ?? "--"}`;
  dom.metricCronDetail.textContent = maxErrors > 0
    ? `最近窗口错误峰值 ${maxErrors}`
    : `${cron.errored ?? 0} 个任务有错误`;
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
  const visibleChecks = slicePanelItems(checks, "health");
  for (const check of visibleChecks) {
    const item = document.createElement("div");
    item.className = `check-item check-${normalizeStatus(check.status)}`;
    item.appendChild(badge(check.status));
    const body = document.createElement("div");
    appendText(body, "div", check.name || "unknown", "item-title");
    appendText(body, "div", check.message || "", "item-meta");
    item.appendChild(body);
    dom.healthList.appendChild(item);
  }
  appendCollapseToggle(dom.healthList, "health", checks.length, () => renderHealth(health));
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
  const inbound = runtime.inbound || {};
  const tasks = runtime.tasks || {};
  const sessionLocks = tasks.session_locks || {};
  const persistedLanes = tasks.persisted_lanes || {};
  const persistedLaneItems = Array.isArray(persistedLanes.items) ? persistedLanes.items : [];
  const taskBroker = tasks.broker || tasks.queue?.broker || {};
  const brokerQueues = Array.isArray(taskBroker.queues) ? taskBroker.queues : [];
  const visibleBrokerQueues = brokerQueues.slice(0, 6);
  const brokerEnabled = Boolean(taskBroker.enabled);
  const brokerBacklog = Number(taskBroker.messages || 0);
  const brokerDeadLetters = Number(taskBroker.dead_letter_messages || 0);
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
      icon: "IN",
      title: "入站队列",
      value: `${inbound.running_tasks ?? 0}/${inbound.max_concurrent_lanes ?? 0}`,
      status: Number(inbound.queued_messages || 0) > 0 ? "warning" : "ok",
      summary: "运行中 / 最大并发 lane",
      chips: [
        `全局队列 ${inbound.global_queue_depth ?? 0}/${inbound.global_queue_limit ?? 0}`,
        `lane ${inbound.lane_count ?? 0}`,
        `排队 ${inbound.queued_messages ?? 0}`,
      ],
      rows: [
        ["运行状态", inbound.running ? "运行中" : "未运行"],
        ["活跃 lane", `${inbound.active_lanes ?? 0}`],
        ["运行任务", `${inbound.running_tasks ?? 0}`],
        ["最老等待", formatSecondsLabel(inbound.oldest_wait_seconds)],
      ],
    },
    {
      icon: "TW",
      title: "后台任务",
      value: `${tasks.queue?.pending ?? 0}/${tasks.queue?.running ?? 0}`,
      status: brokerDeadLetters > 0 || Number(sessionLocks.blocked_session_count || 0) > 0 ? "warning" : "ok",
      summary: "待执行 / 运行中任务",
      chips: [
        `worker ${tasks.running ? "运行中" : "未运行"}`,
        `并发 ${tasks.concurrency ?? 0}`,
        `入站Broker ${brokerEnabled ? "已启用" : "未启用"}`,
        `Broker积压 ${brokerBacklog}`,
        `被锁会话 ${sessionLocks.blocked_session_count ?? 0}`,
        `持久Lane ${persistedLanes.count ?? 0}`,
      ],
      rows: [
        ["worker ID", tasks.worker_id || "--"],
        ["注册任务", (tasks.registered_task_types || []).join(", ") || "无"],
        ["待执行", `${tasks.queue?.pending ?? 0}`],
        ["重试中", `${tasks.queue?.retrying ?? 0}`],
        ["入站 Broker", brokerEnabled ? `${taskBroker.backend || "broker"} 已启用` : "未启用"],
        ["Broker Exchange", taskBroker.exchange || "--"],
        ["Broker 分区", `${taskBroker.partitions ?? 0}`],
        ["Broker Prefetch", `${taskBroker.prefetch ?? 0}`],
        ["Broker 总积压", `${brokerBacklog}`],
        ["Broker 死信", `${brokerDeadLetters}`],
        [
          "分区积压",
          visibleBrokerQueues
            .map((row) => `#${row.partition}:${row.messages}`)
            .join(" | ") || "无",
        ],
        ["被锁会话", `${sessionLocks.blocked_session_count ?? 0}`],
        ["累计跳过", `${sessionLocks.skip_count ?? 0}`],
        [
          "最近跳过",
          (sessionLocks.last_blocked_sessions || [])
            .map((row) => `${row.task_type}:${row.session_key}`)
            .join(" | ") || "无",
        ],
        [
          "持久 Lane",
          persistedLanes.configured
            ? `${persistedLanes.count ?? 0} 条最近 owner`
            : "未接入 PostgreSQL",
        ],
        [
          "最近 owner",
          persistedLaneItems
            .map((row) => `${row.worker_id || "--"}:${row.session_key || "--"}`)
            .join(" | ") || "无",
        ],
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

  const visibleSections = slicePanelItems(sections, "runtime");
  for (const section of visibleSections) {
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
      appendText(row, "code", formatDisplayValue(label, value));
      details.appendChild(row);
    }
    card.appendChild(details);
    dom.runtimeList.appendChild(card);
  }
  appendCollapseToggle(dom.runtimeList, "runtime", sections.length, () => renderRuntime(runtime));
}

function renderInbound(inbound) {
  clearNode(dom.inboundList);
  const configured = Boolean(inbound.configured);
  const lanes = Array.isArray(inbound.lanes) ? inbound.lanes : [];
  dom.inboundSummary.textContent = configured
    ? `运行 ${inbound.running_tasks ?? 0}/${inbound.max_concurrent_lanes ?? 0} · 排队 ${inbound.queued_messages ?? 0}`
    : "未配置";
  if (!configured) {
    dom.inboundList.className = "inbound-list empty";
    dom.inboundList.textContent = "当前没有接入 ChannelRuntime，无法展示入站队列状态。";
    return;
  }

  dom.inboundList.className = "inbound-list";
  const overview = document.createElement("div");
  overview.className = `inbound-overview inbound-${Number(inbound.queued_messages || 0) > 0 ? "warning" : "ok"}`;
  const cards = [
    ["全局入口队列", `${inbound.global_queue_depth ?? 0}/${inbound.global_queue_limit ?? 0}`, "等待进入 lane 分发的消息"],
    ["单车道上限", `${inbound.lane_queue_limit ?? 0}`, "每个 peer/session lane 可积压的消息数"],
    ["运行中任务", `${inbound.running_tasks ?? 0}/${inbound.max_concurrent_lanes ?? 0}`, "当前占用 / 全局并发上限"],
    ["最老等待", formatSecondsLabel(inbound.oldest_wait_seconds), "队列中最早消息等待时间"],
  ];
  for (const [label, value, detail] of cards) {
    const card = document.createElement("article");
    card.className = "inbound-stat";
    appendText(card, "span", label, "trend-label");
    appendText(card, "strong", value, "trend-value");
    appendText(card, "small", detail, "trend-detail");
    overview.appendChild(card);
  }
  dom.inboundList.appendChild(overview);

  if (!lanes.length) {
    appendText(dom.inboundList, "div", "当前没有活跃或积压的入站 lane。", "empty");
    return;
  }

  const laneWrap = document.createElement("div");
  laneWrap.className = "inbound-lanes";
  const visibleLanes = slicePanelItems(lanes, "inbound-lanes");
  for (const lane of visibleLanes) {
    const item = document.createElement("article");
    const tone = Number(lane.queued || 0) > 0 ? "warning" : Number(lane.active || 0) > 0 ? "ok" : "muted";
    item.className = `inbound-lane inbound-lane-${tone}`;
    const head = document.createElement("div");
    head.className = "inbound-lane-head";
    appendText(head, "strong", lane.key || "unknown lane", "item-title");
    head.appendChild(badge(tone));
    item.appendChild(head);
    const meta = [
      `运行中 ${lane.active ?? 0}`,
      `排队 ${lane.queued ?? 0}`,
      `最老等待 ${formatSecondsLabel(lane.oldest_wait_seconds)}`,
    ].join(" · ");
    appendText(item, "div", meta, "item-meta");
    laneWrap.appendChild(item);
  }
  dom.inboundList.appendChild(laneWrap);
  appendCollapseToggle(dom.inboundList, "inbound-lanes", lanes.length, () => renderInbound(inbound));
}

function renderMetrics(summaryPayload, tailPayload) {
  clearNode(dom.metricsHighlights);
  clearNode(dom.metricsTrends);

  const summary = summaryPayload || {};
  const items = Array.isArray(tailPayload?.items) ? tailPayload.items : [];
  const latest = summary.latest || items[items.length - 1] || {};
  const available = Boolean(summary.configured && (summary.available || items.length));
  dom.metricsSummary.textContent = available
    ? `最近 ${summary.count || items.length || 0} 个采样点`
    : "暂无采样";

  if (!available) {
    dom.metricsHighlights.className = "trend-highlight-list empty";
    dom.metricsTrends.className = "trend-grid empty";
    dom.metricsHighlights.textContent = "指标采样尚未产生。等待后台运行一段时间后，这里会出现最近趋势。";
    dom.metricsTrends.textContent = "暂无趋势数据";
    return;
  }

  dom.metricsHighlights.className = "trend-highlight-list";
  dom.metricsTrends.className = "trend-grid";

  const delivery = summary.delivery || {};
  const laneMetrics = summary.lanes || {};
  const eventMetrics = summary.events || {};
  const cronMetrics = summary.cron || {};
  const profileMetrics = summary.profiles || {};

  const highlights = [
    {
      title: "最近堆积峰值",
      value: `${delivery.max_pending ?? 0} 条`,
      detail: `失败峰值 ${delivery.max_failed ?? 0} 条，立即可重试峰值 ${delivery.max_retry_ready ?? 0} 条`,
      tone: Number(delivery.max_failed || 0) > 0 ? "warning" : "ok",
    },
    {
      title: "最近错误峰值",
      value: `${eventMetrics.max_errors_5m ?? 0} 次`,
      detail: `拒绝 ${eventMetrics.max_rejected_5m ?? 0} 次，投递失败 ${eventMetrics.max_delivery_failed_5m ?? 0} 次`,
      tone: Number(eventMetrics.max_errors_5m || 0) >= 3 ? "critical" : Number(eventMetrics.max_errors_5m || 0) > 0 ? "warning" : "ok",
    },
    {
      title: "并发车道压力",
      value: `${laneMetrics.max_queued ?? 0} 条`,
      detail: `活跃峰值 ${laneMetrics.max_active ?? 0}，单车道最深 ${laneMetrics.max_queue_depth ?? 0}`,
      tone: Number(laneMetrics.max_queued || 0) >= 5 ? "warning" : "ok",
    },
    {
      title: "当前快照",
      value: formatTimestamp(latest.time || latest.timestamp),
      detail: `Cron 错误峰值 ${cronMetrics.max_errored ?? 0}，可用 Profile 峰值 ${profileMetrics.max_available ?? 0}`,
      tone: "ok",
    },
  ];

  for (const item of highlights) {
    const card = document.createElement("article");
    card.className = `trend-highlight trend-${item.tone}`;
    appendText(card, "span", item.title, "trend-label");
    appendText(card, "strong", item.value, "trend-value");
    appendText(card, "small", item.detail, "trend-detail");
    dom.metricsHighlights.appendChild(card);
  }

  const trendCards = [
    buildTrendCard({
      title: "投递队列",
      summary: `当前待投递 ${latest.delivery?.pending ?? 0} 条，失败 ${latest.delivery?.failed ?? 0} 条`,
      points: items.map((row) => ({
        label: formatTrendPointTime(row.time || row.timestamp),
        value: Number(row.delivery?.pending || 0),
      })),
      metricLabel: "待投递",
      detailRows: [
        ["最近峰值", `${delivery.max_pending ?? 0} 条`],
        ["失败峰值", `${delivery.max_failed ?? 0} 条`],
        ["最老待投递", formatSecondsLabel(delivery.max_oldest_pending_age_seconds)],
      ],
      tone: Number(delivery.max_failed || 0) > 0 ? "warning" : "ok",
    }),
    buildTrendCard({
      title: "错误热度",
      summary: `最近 5 分钟错误峰值 ${eventMetrics.max_errors_5m ?? 0} 次`,
      points: items.map((row) => ({
        label: formatTrendPointTime(row.time || row.timestamp),
        value: Number(row.events?.errors_5m || 0),
      })),
      metricLabel: "错误数",
      detailRows: [
        ["拒绝峰值", `${eventMetrics.max_rejected_5m ?? 0} 次`],
        ["工具失败峰值", `${eventMetrics.max_tool_failed_5m ?? 0} 次`],
        ["Cron 失败峰值", `${eventMetrics.max_cron_failed_5m ?? 0} 次`],
      ],
      tone: Number(eventMetrics.max_errors_5m || 0) >= 3 ? "critical" : Number(eventMetrics.max_errors_5m || 0) > 0 ? "warning" : "ok",
    }),
    buildTrendCard({
      title: "并发车道",
      summary: `当前排队 ${latest.lanes?.queued ?? 0} 条，活跃 ${latest.lanes?.active ?? 0} 条`,
      points: items.map((row) => ({
        label: formatTrendPointTime(row.time || row.timestamp),
        value: Number(row.lanes?.queued || 0),
      })),
      metricLabel: "排队数",
      detailRows: [
        ["活跃峰值", `${laneMetrics.max_active ?? 0} 条`],
        ["排队峰值", `${laneMetrics.max_queued ?? 0} 条`],
        ["单车道最深", `${laneMetrics.max_queue_depth ?? 0}`],
      ],
      tone: Number(laneMetrics.max_queued || 0) >= 5 ? "warning" : "ok",
    }),
    buildTrendCard({
      title: "定时任务与 Profile",
      summary: `当前 Cron 错误 ${latest.cron?.errored ?? 0} 个，可用 Profile ${latest.profiles?.available ?? 0} 个`,
      points: items.map((row) => ({
        label: formatTrendPointTime(row.time || row.timestamp),
        value: Number(row.cron?.errored || 0) + Number(row.profiles?.cooling_down || 0),
      })),
      metricLabel: "异常负载",
      detailRows: [
        ["Cron 错误峰值", `${cronMetrics.max_errored ?? 0} 个`],
        ["Cron 启用峰值", `${cronMetrics.max_enabled ?? 0} 个`],
        ["Profile 冷却峰值", `${profileMetrics.max_cooling_down ?? 0} 个`],
      ],
      tone: Number(cronMetrics.max_errored || 0) > 0 || Number(profileMetrics.max_cooling_down || 0) > 0 ? "warning" : "ok",
    }),
  ];

  for (const card of trendCards) {
    dom.metricsTrends.appendChild(card);
  }
}

function renderAlerts(activePayload, historyPayload) {
  clearNode(dom.alertsActive);
  clearNode(dom.alertsHistory);

  const activeItems = Array.isArray(activePayload?.items) ? activePayload.items : [];
  const historyItems = Array.isArray(historyPayload?.items) ? historyPayload.items.slice().reverse() : [];
  const target = activePayload?.notification_target || {};
  dom.alertsSummary.textContent = activeItems.length
    ? `${activeItems.length} 条活跃告警`
    : target.peer_id_configured
      ? "当前无活跃告警"
      : "未配置主动通知";
  dom.alertsHistorySummary.textContent = `${historyItems.length} 条历史`;

  dom.alertsActive.className = activeItems.length ? "alert-state-list" : "alert-state-list empty";
  if (!activeItems.length) {
    dom.alertsActive.textContent = target.peer_id_configured
      ? "当前没有活跃告警。告警触发后会在这里显示，并可通过通知目标主动发送。"
      : "当前没有活跃告警，且尚未配置飞书告警通知目标。";
  } else {
    const visibleActiveItems = slicePanelItems(activeItems, "alerts-active");
    for (const item of visibleActiveItems) {
      const card = document.createElement("article");
      card.className = `alert-card alert-${normalizeStatus(item.severity)}`;
      const head = document.createElement("div");
      head.className = "alert-card-head";
      head.appendChild(badge(item.severity));
      const title = document.createElement("div");
      appendText(title, "strong", item.title || item.rule_id || "告警", "item-title");
      appendText(
        title,
        "small",
        `${item.description || ""} · 持续 ${formatAlertDuration(item.active_since)} · 当前值 ${item.current_value ?? "--"} / 阈值 ${item.threshold ?? "--"}`,
        "item-meta",
      );
      head.appendChild(title);
      card.appendChild(head);
      appendText(card, "div", item.last_message || "暂无补充说明", "item-title");
      appendText(
        card,
        "div",
        `通知状态：${item.last_notified_time ? `上次通知 ${formatTimestamp(item.last_notified_time)}` : "尚未发送"}${item.last_notification_error ? ` · 发送失败：${item.last_notification_error}` : ""}`,
        "item-meta",
      );
      const details = document.createElement("details");
      details.className = "runtime-details";
      const summary = document.createElement("summary");
      summary.textContent = "查看技术详情";
      details.appendChild(summary);
      const rows = [
        ["规则 ID", item.rule_id],
        ["首次触发", formatTimestamp(item.active_since)],
        ["最近评估", formatTimestamp(item.last_evaluated_at)],
        ["连续命中", String(item.consecutive_hits ?? 0)],
        ["通知目标", `${target.channel || "--"}:${target.account_id || "--"} -> ${target.agent_id || "--"}`],
        ["技术元数据", JSON.stringify(item.metadata || {}, null, 2)],
      ];
      for (const [label, value] of rows) {
        const row = document.createElement("div");
        row.className = "kv-row";
        appendText(row, "span", label);
        appendText(row, "code", formatDisplayValue(label, value));
        details.appendChild(row);
      }
      card.appendChild(details);
      dom.alertsActive.appendChild(card);
    }
    appendCollapseToggle(
      dom.alertsActive,
      "alerts-active",
      activeItems.length,
      () => renderAlerts(activePayload, historyPayload),
    );
  }

  dom.alertsHistory.className = historyItems.length ? "alert-history-list" : "alert-history-list empty";
  if (!historyItems.length) {
    dom.alertsHistory.textContent = "最近没有告警触发、提醒或恢复记录。";
    return;
  }
  const visibleHistoryItems = slicePanelItems(historyItems, "alerts-history");
  for (const item of visibleHistoryItems) {
    const card = document.createElement("article");
    const event = String(item.event || "");
    const severity = item.rule?.severity || (event === "recovered" ? "ok" : "warning");
    card.className = `alert-card alert-${normalizeStatus(severity)}`;
    const head = document.createElement("div");
    head.className = "alert-card-head";
    head.appendChild(badge(event === "recovered" ? "ok" : severity));
    const title = document.createElement("div");
    appendText(title, "strong", `${alertEventLabel(event)}：${item.rule?.title || item.rule?.id || "告警"}`, "item-title");
    appendText(title, "small", `${formatTimestamp(item.time || item.timestamp)} · 当前值 ${item.value ?? "--"} / 阈值 ${item.rule?.threshold ?? "--"}`, "item-meta");
    head.appendChild(title);
    card.appendChild(head);
    appendText(card, "div", item.message || "无说明", "item-title");
    appendText(card, "div", `规则 ${item.rule?.id || "--"} · 等级 ${item.rule?.severity || "--"}`, "item-meta");
    dom.alertsHistory.appendChild(card);
  }
  appendCollapseToggle(
    dom.alertsHistory,
    "alerts-history",
    historyItems.length,
    () => renderAlerts(activePayload, historyPayload),
  );
}

function formatAlertDuration(value) {
  if (!value) {
    return "--";
  }
  return formatDuration(Math.max(0, (Date.now() / 1000) - Number(value)));
}

function alertEventLabel(event) {
  return {
    triggered: "告警触发",
    reminded: "告警持续",
    recovered: "告警恢复",
  }[event] || "告警事件";
}

function buildTrendCard({ title, summary, points, metricLabel, detailRows, tone }) {
  const card = document.createElement("article");
  card.className = `trend-card trend-${tone || "ok"}`;

  const head = document.createElement("div");
  head.className = "trend-card-head";
  const titleNode = document.createElement("div");
  appendText(titleNode, "strong", title, "item-title");
  appendText(titleNode, "small", summary, "item-meta");
  head.appendChild(titleNode);
  if (points.length) {
    appendText(head, "span", `${points[points.length - 1].value}`, "trend-current");
  }
  card.appendChild(head);

  card.appendChild(buildSparkline(points, metricLabel));

  const rows = document.createElement("div");
  rows.className = "trend-rows";
  for (const [label, value] of detailRows) {
    const row = document.createElement("div");
    row.className = "kv-row";
    appendText(row, "span", label);
    appendText(row, "code", formatDisplayValue(label, value));
    rows.appendChild(row);
  }
  card.appendChild(rows);
  return card;
}

function buildSparkline(points, metricLabel) {
  const wrap = document.createElement("div");
  wrap.className = "sparkline-card";
  if (!points.length) {
    wrap.classList.add("empty");
    wrap.textContent = "暂无采样点";
    return wrap;
  }

  const values = points.map((point) => Number(point.value || 0));
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = Math.max(max - min, 1);
  const coords = values.map((value, index) => {
    const x = points.length === 1 ? 0 : (index / (points.length - 1)) * 100;
    const y = 100 - ((value - min) / range) * 100;
    return `${x},${y}`;
  }).join(" ");

  wrap.innerHTML = `
    <div class="sparkline-meta">
      <span>${metricLabel}</span>
      <strong>${values[values.length - 1]}</strong>
    </div>
    <svg class="sparkline" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      <polyline class="sparkline-fill" points="0,100 ${coords} 100,100"></polyline>
      <polyline class="sparkline-line" points="${coords}"></polyline>
    </svg>
    <div class="sparkline-axis">
      <span>${points[0].label}</span>
      <span>${points[points.length - 1].label}</span>
    </div>
  `;
  return wrap;
}

function formatTrendPointTime(value) {
  const date = parseDateValue(value);
  if (!date) {
    return "--";
  }
  const parts = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date).reduce((acc, part) => {
    if (part.type !== "literal") {
      acc[part.type] = part.value;
    }
    return acc;
  }, {});
  return `${parts.hour}:${parts.minute}`;
}

function formatSecondsLabel(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return formatDuration(Number(value));
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
  const visibleItems = slicePanelItems(items, "delivery");
  for (const item of visibleItems) {
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
  appendCollapseToggle(
    dom.deliveryTable,
    "delivery",
    items.length,
    () => renderDelivery(deliveryList),
  );
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
  const groups = groupCronJobs(jobs);
  const visibleGroups = slicePanelItems(groups, "cron-groups");
  for (const group of visibleGroups) {
    const section = document.createElement("section");
    section.className = "cron-group";
    const head = document.createElement("div");
    head.className = "cron-group-head";
    appendText(head, "strong", group.title);
    appendText(head, "span", `${group.jobs.filter((job) => job.enabled).length}/${group.jobs.length} 已启用`);
    section.appendChild(head);
    const visibleJobs = slicePanelItems(group.jobs, `cron-group-${group.title}`);
    for (const job of visibleJobs) {
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
    appendCollapseToggle(
      section,
      `cron-group-${group.title}`,
      group.jobs.length,
      () => renderCron(cronJobs),
    );
    dom.cronList.appendChild(section);
  }
  appendCollapseToggle(dom.cronList, "cron-groups", groups.length, () => renderCron(cronJobs));
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
    panelKey: "events",
    container: dom.eventsList,
    summary: dom.eventsSummary,
    payload,
    emptyText: "暂无运行事件。发送一条消息、触发 Cron 或 flush 投递队列后会出现链路事件。",
    summaryLabel: "条事件",
    collapsible: true,
    expanded: isPanelExpanded("events"),
    collapsedLimit: DEFAULT_PANEL_LIMIT,
    onToggle: () => {
      togglePanelExpanded("events");
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
  const visibleTraces = slicePanelItems(traces, "traces");
  for (const trace of visibleTraces) {
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
  appendCollapseToggle(dom.tracesList, "traces", traces.length, () => renderTraces(payload));
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
    panelKey: "errors",
    container: dom.errorsList,
    summary: dom.errorsSummary,
    payload,
    emptyText: "最近没有错误、失败或拒绝事件。",
    summaryLabel: "条错误",
    collapsible: true,
    expanded: isPanelExpanded("errors"),
    collapsedLimit: DEFAULT_PANEL_LIMIT,
    onToggle: () => {
      togglePanelExpanded("errors");
      renderErrors(payload);
    },
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
  const visibleItems = slicePanelItems(items, "memories");
  for (const item of visibleItems) {
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
  appendCollapseToggle(dom.memoryList, "memories", items.length, () => renderMemories(payload));
}

function renderTasks(payload) {
  clearNode(dom.tasksList);
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const counts = items.reduce((acc, item) => {
    const status = normalizeStatus(item.status || "pending");
    acc[status] = (acc[status] || 0) + 1;
    return acc;
  }, {});
  dom.tasksSummary.textContent = items.length
    ? `${items.length} 条任务 · ${counts.running || 0} 执行中 / ${counts.failed || 0} 失败`
    : "暂无任务";
  dom.tasksList.className = items.length ? "task-list" : "task-list empty";
  if (!items.length) {
    dom.tasksList.textContent = "最近没有后台任务。Cron、Heartbeat 或长任务 Skill 入队后会显示在这里。";
    return;
  }
  const visibleItems = slicePanelItems(items, "tasks");
  for (const task of visibleItems) {
    const card = document.createElement("article");
    const status = normalizeStatus(task.status || "pending");
    card.className = `task-item task-${status}`;
    const head = document.createElement("div");
    head.className = "task-head";
    head.appendChild(badge(status));
    const title = document.createElement("div");
    appendText(title, "strong", taskTitle(task), "task-title");
    appendText(
      title,
      "small",
      [
        `来源 ${task.source || "--"}`,
        `智能体 ${task.agent_id || "--"}`,
        `优先级 ${task.priority ?? "--"}`,
        `重试 ${task.retry_count ?? 0} 次`,
      ].join(" · "),
      "task-meta",
    );
    head.appendChild(title);
    appendText(head, "span", formatShortTime(task.updated_at), "event-time");
    card.appendChild(head);
    appendText(card, "p", task.payload_preview || task.result_preview || task.error || "无预览内容", "task-preview");
    appendText(
      card,
      "div",
      `创建：${formatTimestamp(task.created_at)} · 更新：${formatTimestamp(task.updated_at)} · 会话：${task.session_key || "--"}`,
      "task-meta",
    );
    if (task.error) {
      appendText(card, "pre", task.error, "event-error");
    }
    const actions = document.createElement("div");
    actions.className = "row-actions";
    if (["pending", "running", "retrying"].includes(status)) {
      appendButton(actions, "取消", "button button-small button-danger", () => cancelTask(task.id));
    }
    if (["failed", "cancelled"].includes(status)) {
      appendButton(actions, "重试", "button button-small", () => retryTask(task.id));
    }
    if (actions.children.length) {
      card.appendChild(actions);
    }
    dom.tasksList.appendChild(card);
  }
  appendCollapseToggle(dom.tasksList, "tasks", items.length, () => renderTasks(payload));
}

function taskTitle(task) {
  const typeLabels = {
    agent_inbound: "长任务消息",
    cron: "定时任务",
    heartbeat: "心跳任务",
  };
  return `${typeLabels[task.task_type] || task.task_type || "后台任务"} · ${shortId(task.id, 10, 4)}`;
}

function renderEventList({
  panelKey = "events",
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
      appendText(row, "code", formatDisplayValue(label, value));
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
      ? "收起"
      : `展开剩余 ${items.length - collapsedLimit} 条`;
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

async function cancelTask(taskId) {
  if (!taskId) {
    return;
  }
  const confirmed = confirmAction(
    `确认取消后台任务 ${taskId}？`,
    "取消仅对尚未完成的任务生效；已进入外部调用的任务可能需要等待当前步骤结束。",
  );
  if (!confirmed) {
    return;
  }
  try {
    const result = await rpc("tasks.cancel", { task_id: taskId });
    showToast(result.ok ? `已取消任务 ${taskId}` : `任务 ${taskId} 当前状态不可取消`, result.ok ? "success" : "warning");
    await refreshAll();
  } catch (error) {
    showAlert(error.message);
  }
}

async function retryTask(taskId) {
  if (!taskId) {
    return;
  }
  const confirmed = confirmAction(
    `确认重试后台任务 ${taskId}？`,
    "重试会把失败或已取消任务重新放回 worker 可执行队列。",
  );
  if (!confirmed) {
    return;
  }
  try {
    const result = await rpc("tasks.retry", { task_id: taskId });
    showToast(result.ok ? `已请求重试任务 ${taskId}` : `任务 ${taskId} 当前状态不可重试`, result.ok ? "success" : "warning");
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

async function republishDelivery() {
  const confirmed = confirmAction(
    "确认重建 RabbitMQ 投递队列？",
    "该操作会从 PostgreSQL/本地事实状态重新发布 pending 和 retrying 投递引用，不会复制完整正文到 RabbitMQ。",
  );
  if (!confirmed) {
    return;
  }
  try {
    const result = await rpc("delivery.republish", {
      include_pending: true,
      include_retrying: true,
    });
    showToast(`已重新发布 ${result.published ?? 0} 条投递引用`);
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
  dom.deliveryRepublishBtn.addEventListener("click", republishDelivery);
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
