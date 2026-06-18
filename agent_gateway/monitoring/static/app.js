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
  if (!value) {
    return "--";
  }
  if (typeof value === "number") {
    return new Date(value * 1000).toLocaleString("zh-CN");
  }
  return String(value);
}

function normalizeStatus(status) {
  if (status === "ok") {
    return "ok";
  }
  if (status === "degraded" || status === "warning") {
    return "warning";
  }
  if (status === "unhealthy" || status === "critical" || status === "error") {
    return "critical";
  }
  return "muted";
}

function badge(status) {
  const span = document.createElement("span");
  span.className = `status-badge status-${normalizeStatus(status)}`;
  span.textContent = status || "unknown";
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
    const [health, runtime, deliveryStats, deliveryList, cronJobs, events, errors] = await Promise.all([
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
    ]);
    renderSummary(health, runtime, deliveryStats);
    renderIssues(buildIssues(health, runtime, deliveryStats));
    renderHealth(health);
    renderRuntime(runtime);
    renderTraces(events);
    renderEvents(events);
    renderErrors(errors);
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
  dom.metricHealthDetail.textContent = `${health.summary?.critical || 0} critical / ${health.summary?.warning || 0} warning`;
  const healthCard = dom.metricHealth.closest(".metric-health");
  healthCard.classList.remove("is-ok", "is-warning", "is-critical");
  healthCard.classList.add(`is-${normalizeStatus(health.status)}`);

  dom.metricUptime.textContent = formatDuration(server.uptime_seconds);
  dom.metricServer.textContent = server.running ? "server running" : "server not running";
  dom.metricDelivery.textContent = `${delivery.pending ?? "--"} / ${delivery.failed ?? "--"}`;
  dom.metricDeliveryDetail.textContent = `${delivery.retry_ready ?? 0} 条可立即重试`;
  dom.metricChannels.textContent = `${channels.active ?? "--"} / ${channels.count ?? "--"}`;
  dom.metricChannelsDetail.textContent = "active / total";
  dom.metricProfiles.textContent = `${profiles.available ?? "--"} / ${profiles.count ?? "--"}`;
  dom.metricProfilesDetail.textContent = "available / total";
  dom.metricCron.textContent = `${cron.enabled ?? "--"} / ${cron.count ?? "--"}`;
  dom.metricCronDetail.textContent = `${cron.errored ?? 0} 个任务有错误`;
}

function renderHealth(health) {
  clearNode(dom.healthList);
  const checks = (Array.isArray(health.checks) ? health.checks : [])
    .slice()
    .sort((left, right) => severityWeight(left.status) - severityWeight(right.status));
  dom.healthList.className = checks.length ? "check-list" : "check-list empty";
  dom.healthSummary.textContent = `${health.status || "unknown"} | ok ${health.summary?.ok || 0}`;
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
  dom.runtimeUpdated.textContent = new Date().toLocaleTimeString("zh-CN");

  const channels = runtime.channels || {};
  const profiles = runtime.profiles || {};
  const features = runtime.features || {};
  const proactive = features.proactive_target || {};
  const paths = runtime.paths || {};
  const sections = [
    {
      icon: "AG",
      title: "Agents",
      value: `${runtime.agents?.count ?? 0} 个`,
      status: Number(runtime.agents?.count || 0) > 0 ? "ok" : "critical",
      summary: `${runtime.bindings?.count ?? 0} 条路由绑定`,
      chips: [
        `agents ${runtime.agents?.count ?? 0}`,
        `bindings ${runtime.bindings?.count ?? 0}`,
      ],
      rows: [
        ["Agent IDs", (runtime.agents?.ids || []).join(", ") || "无"],
        ["Route Bindings", `${runtime.bindings?.count ?? 0} 条`],
      ],
    },
    {
      icon: "CH",
      title: "Channels",
      value: `${channels.active ?? 0}/${channels.count ?? 0}`,
      status: Number(channels.active || 0) > 0 ? "ok" : "warning",
      summary: "活跃通道 / 已配置通道",
      chips: [
        `active ${channels.active ?? 0}`,
        `total ${channels.count ?? 0}`,
      ],
      rows: (channels.items || []).map((row) => [
        `${row.channel}:${row.account_id}`,
        `${row.active ? "active" : "inactive"} | enabled=${row.enabled} | token=${row.has_token ? "configured" : "missing"}`,
      ]),
    },
    {
      icon: "AI",
      title: "Profiles",
      value: `${profiles.available ?? 0}/${profiles.count ?? 0}`,
      status: Number(profiles.available || 0) > 0 ? "ok" : "critical",
      summary: "可用模型 Profile / 总数",
      chips: [
        `available ${profiles.available ?? 0}`,
        `total ${profiles.count ?? 0}`,
      ],
      rows: (profiles.items || []).map((row) => [
        row.name || "profile",
        `key=${row.has_key ? "configured" : "missing"} | cooldown=${row.cooldown_remaining || 0}s`,
      ]),
    },
    {
      icon: "FX",
      title: "Features",
      value: features.web_search_enabled ? "Search On" : "Search Off",
      status: features.web_search_enabled && !features.web_search_has_key ? "warning" : "ok",
      summary: proactive.peer_id_configured ? "主动投递目标已配置" : "主动投递目标未完整配置",
      chips: [
        features.web_search_enabled ? "web search on" : "web search off",
        proactive.peer_id_configured ? "proactive ready" : "proactive missing",
      ],
      rows: [
        [
          "Web Search",
          `${features.web_search_provider || "--"} | key=${features.web_search_has_key ? "configured" : "missing"}`,
        ],
        [
          "Proactive",
          `${proactive.channel || "--"}:${proactive.account_id || "--"} -> ${proactive.agent_id || "--"} | peer=${proactive.peer_id_configured ? "configured" : "missing"}`,
        ],
      ],
    },
    {
      icon: "FS",
      title: "Paths",
      value: paths.workspace_exists && paths.data_dir_exists && paths.config_dir_exists ? "OK" : "Check",
      status: paths.workspace_exists && paths.data_dir_exists && paths.config_dir_exists ? "ok" : "critical",
      summary: "workspace / data / config",
      chips: [
        paths.workspace_exists ? "workspace ok" : "workspace missing",
        paths.data_dir_exists ? "data ok" : "data missing",
        paths.config_dir_exists ? "config ok" : "config missing",
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
    summary.textContent = "查看详情";
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
    dom.deliveryTable.className = "table-card empty";
    dom.deliveryTable.textContent = `当前 ${deliveryList.state || dom.deliveryState.value} 队列为空`;
    return;
  }
  dom.deliveryTable.className = "table-card";
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  ["ID", "状态", "通道", "目标", "重试", "下次重试", "错误", "内容预览", "操作"].forEach((label) => {
    appendText(headerRow, "th", label);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const item of items) {
    const row = document.createElement("tr");
    appendText(row, "td", item.id || "", "mono");
    const stateCell = document.createElement("td");
    stateCell.appendChild(badge(item.state));
    row.appendChild(stateCell);
    appendText(row, "td", item.channel || "--");
    appendText(row, "td", item.to || "--", "mono");
    appendText(row, "td", String(item.retry_count ?? 0));
    appendText(
      row,
      "td",
      item.retry_ready ? "ready" : `${item.next_retry_in_seconds ?? "--"}s`,
    );
    const classification = classifyDeliveryError(item);
    const errorCell = document.createElement("td");
    const errorWrap = document.createElement("div");
    errorWrap.className = "error-summary";
    errorWrap.appendChild(badge(classification.severity));
    appendText(errorWrap, "strong", classification.label);
    appendText(errorWrap, "small", classification.suggestion);
    if (item.last_error) {
      appendText(errorWrap, "span", item.last_error, "preview");
    }
    errorCell.appendChild(errorWrap);
    row.appendChild(errorCell);
    appendText(row, "td", item.text || item.text_preview || "", "preview");

    const actions = document.createElement("td");
    const actionWrap = document.createElement("div");
    actionWrap.className = "row-actions";
    appendButton(actionWrap, "详情", "button button-small", () => showDeliveryDetail(item));
    appendButton(actionWrap, "复制ID", "button button-small", () => copyText(item.id || ""));
    appendButton(actionWrap, "重试", "button button-small", () => retryDelivery(item.id, classification));
    appendButton(actionWrap, "丢弃", "button button-small button-danger", () => discardDelivery(item.id, item.state, classification));
    actions.appendChild(actionWrap);
    row.appendChild(actions);
    tbody.appendChild(row);
  }
  table.appendChild(tbody);
  dom.deliveryTable.appendChild(table);
}

function renderCron(cronJobs) {
  clearNode(dom.cronList);
  const jobs = Array.isArray(cronJobs) ? cronJobs : [];
  dom.cronList.className = jobs.length ? "cron-list" : "cron-list empty";
  dom.cronSummary.textContent = `${jobs.filter((job) => job.enabled).length}/${jobs.length} enabled`;
  if (!jobs.length) {
    dom.cronList.textContent = "没有 Cron 任务";
    return;
  }
  for (const job of jobs) {
    const item = document.createElement("div");
    item.className = "cron-item";
    const body = document.createElement("div");
    appendText(body, "div", `${job.name || job.id || "unnamed"} (${job.id || "--"})`, "item-title");
    appendText(
      body,
      "div",
      `enabled=${Boolean(job.enabled)} | errors=${job.errors ?? 0} | next=${formatTimestamp(job.next_run)} | last=${formatTimestamp(job.last_run)}`,
      "item-meta",
    );
    const trigger = document.createElement("button");
    trigger.className = "button button-small";
    trigger.type = "button";
    trigger.textContent = "立即触发";
    trigger.disabled = !job.id;
    trigger.addEventListener("click", () => triggerCron(job.id, job.name || job.id));
    item.append(body, trigger);
    dom.cronList.appendChild(item);
  }
}

function renderEvents(payload) {
  renderEventList({
    container: dom.eventsList,
    summary: dom.eventsSummary,
    payload,
    emptyText: "暂无运行事件。发送一条消息、触发 Cron 或 flush 投递队列后会出现链路事件。",
    summaryLabel: "条事件",
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
    appendText(title, "strong", trace.correlationId, "mono");
    appendText(
      title,
      "small",
      `${trace.events.length} events | ${trace.components.join(", ") || "no component"} | ${formatTimestamp(trace.start)} -> ${formatTimestamp(trace.end)}`,
    );
    summary.appendChild(title);
    appendText(summary, "span", trace.lastType || "event", "trace-last-type");
    details.appendChild(summary);
    if (trace.lastError) {
      appendText(details, "pre", trace.lastError, "event-error");
    }
    const timeline = document.createElement("div");
    timeline.className = "trace-timeline";
    for (const event of trace.events) {
      const row = document.createElement("div");
      row.className = `trace-event trace-event-${normalizeStatus(event.status)}`;
      appendText(row, "span", formatTimestamp(event.timestamp), "event-time");
      appendText(row, "strong", event.type || "event");
      appendText(
        row,
        "small",
        [
          event.component || "",
          event.agent_id ? `agent=${event.agent_id}` : "",
          event.delivery_id ? `delivery=${event.delivery_id}` : "",
          event.job_id ? `job=${event.job_id}` : "",
        ].filter(Boolean).join(" | ") || event.message || "",
      );
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
      severity: errorEvents.length ? "critical" : hasWarning ? "warning" : "ok",
      components: [...new Set(sorted.map((event) => event.component).filter(Boolean))],
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

function renderEventList({ container, summary, payload, emptyText, summaryLabel }) {
  clearNode(container);
  const items = Array.isArray(payload?.items) ? payload.items.slice().reverse() : [];
  summary.textContent = `${items.length} ${summaryLabel}`;
  container.className = items.length ? "event-list" : "event-list empty";
  if (!items.length) {
    container.textContent = emptyText;
    return;
  }
  for (const event of items) {
    const item = document.createElement("div");
    item.className = `event-item event-${normalizeStatus(event.status)}`;
    const head = document.createElement("div");
    head.className = "event-head";
    head.appendChild(badge(event.status || "ok"));
    appendText(head, "strong", event.type || "event");
    appendText(head, "span", formatTimestamp(event.timestamp), "event-time");
    item.appendChild(head);

    appendText(item, "div", event.message || "", "item-title");
    const meta = [
      event.component ? `component=${event.component}` : "",
      event.correlation_id ? `corr=${event.correlation_id}` : "",
      event.agent_id ? `agent=${event.agent_id}` : "",
      event.session_key ? `session=${event.session_key}` : "",
      event.channel ? `channel=${event.channel}` : "",
      event.delivery_id ? `delivery=${event.delivery_id}` : "",
      event.job_id ? `job=${event.job_id}` : "",
    ].filter(Boolean);
    appendText(item, "div", meta.join(" | ") || "no context", "item-meta");
    if (event.error) {
      appendText(item, "pre", event.error, "event-error");
    }
    const details = document.createElement("details");
    details.className = "runtime-details";
    const detailsSummary = document.createElement("summary");
    detailsSummary.textContent = "事件详情";
    details.appendChild(detailsSummary);
    const row = document.createElement("div");
    row.className = "kv-row";
    appendText(row, "span", "metadata");
    appendText(row, "code", JSON.stringify(event.metadata || {}, null, 2));
    details.appendChild(row);
    item.appendChild(details);
    container.appendChild(item);
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
    ["delivery_id", item.id || ""],
    ["state", item.state || ""],
    ["channel", item.channel || ""],
    ["to", item.to || ""],
    ["error_type", classification.code],
    ["suggestion", classification.suggestion],
    ["retry_count", String(item.retry_count ?? 0)],
    ["retry_ready", String(Boolean(item.retry_ready))],
    ["next_retry_at", formatTimestamp(item.next_retry_at)],
    ["enqueued_at", formatTimestamp(item.enqueued_at)],
    ["last_error", item.last_error || ""],
    ["text", item.text || item.text_preview || ""],
    ["metadata", JSON.stringify(item.metadata || {}, null, 2)],
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
