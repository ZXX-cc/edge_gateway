const $ = (id) => document.getElementById(id);

let snapshot = null;
let workflows = [];
let activeView = "dashboard";
let lastCommandResult = null;
let retestLogs = [];

const keyStatusLabels = {
  waiting: "待验证",
  active: "有数据",
  ok: "通过",
  warn: "注意",
  fail: "失败",
};

const keyStageOrder = {P0: 0, P1: 1, P2: 2};

const keyPointActions = {
  mqtt_link: {label: "查核心属性", type: "action", ref: "dock:0"},
  apidock3_cmd: {label: "打开命令台", type: "view", view: "dock"},
  sitl_osd: {label: "看飞行日志", type: "view", view: "ops"},
  real_wayline: {label: "运行航线复测", type: "retest", item: "real-hd-wayline"},
  flight_control: {label: "运行飞控复测", type: "retest", item: "basic-flight-sitl"},
  dock_v13: {label: "运行机巢复测", type: "retest", item: "nest-gateway-live"},
  drc: {label: "运行 DRC 复测", type: "retest", item: "drc-mavlink-sitl"},
  hms_log: {label: "运行日志复测", type: "retest", item: "retention-records"},
  trace_nonblock: {label: "运行非阻塞探针", type: "retest", item: "non-blocking-person2"},
};

const viewMeta = {
  dashboard: ["飞行总览", "查看真实 SITL/QGC 航线、飞行器遥测和最近命令状态。"],
  dock: ["APIDock3 命令", "直接通过云端 API topic 下发 Dock3 services、property 和 DRC 命令。"],
  mission: ["真实航线", "先用 QSTABILIZE/QLAN 前置解锁，再下发 HD 航线准备、执行、进度查询。"],
  drc: ["DRC 飞控", "进入控制、心跳、速度推送、退出控制。"],
  ops: ["飞行日志", "按时间查看飞行过程中的电量、高度、经纬度、模式、航线进度和回包。"],
  retest: ["复测留证", "只保留真实 SITL、APIDock3、DRC、机场机构和非阻塞留证项。"],
  raw: ["原始报文", "需要排障时查看完整 MQTT payload。"],
};

const retestItems = [
  {
    id: "real-hd-wayline",
    category: "SITL",
    title: "SITL/QGC 真实 HD 航线执行",
    status: "needs_data",
    command: "python tools/mqtt_dock3_wayline_test.py --host 192.168.2.188 --port 1883 --sn EDGEGW001 --timeout 20 --quiet-stale",
    expect: "先通过 set_flight_mode(mode=QLAN, arm=true) 解锁，再让 QGC 中看到真实航线任务，flighttask_prepare/execute 成功，OSD 经纬度/高度随航线变化。",
    evidence: "set_flight_mode 回包、SITL 日志、MQTT flighttask_progress、飞行器 OSD 电量/高度/经纬度。",
  },
  {
    id: "cmd-advanced",
    category: "APIDock3",
    title: "APIDock3/CMD 远程调试",
    status: "needs_data",
    command: "python tools/mqtt_dock3_cmd_test.py --host 192.168.2.188 --port 1883 --sn EDGEGW001 --timeout 20 --quiet-stale",
    expect: "可直接从页面或脚本通过 Dock3 topic 下发命令，并看到 result、tid、进度事件。",
    evidence: "services_reply、progress event、operation record、拒绝原因。",
  },
  {
    id: "flight-mode-sitl",
    category: "飞控",
    title: "SITL 飞行模式真实复测",
    status: "needs_data",
    command: "python tools/person2_gap_test.py --phase flight-modes --host 192.168.2.188 --port 1883 --sn EDGEGW001 --drone-sn DRONESIM001",
    expect: "GUIDED/RTL/LAND 下发成功，且测试期间能收到飞行器 OSD 作为真实 SITL 在线证据。",
    evidence: "services_reply result、OSD mode_code/gear、FlightControl 日志。",
  },
  {
    id: "basic-flight-sitl",
    category: "飞控",
    title: "SITL 基础飞控动作",
    status: "needs_data",
    command: "python tools/person2_gap_test.py --phase basic-flight --host 192.168.2.188 --port 1883 --sn EDGEGW001 --drone-sn DRONESIM001",
    expect: "GUIDED、take_off、go_home、landing 均有回包，并且飞行器 OSD 可留证。",
    evidence: "services_reply result、OSD 经纬度/高度/模式、FlightControl 日志。",
  },
  {
    id: "drc-mavlink-sitl",
    category: "DRC",
    title: "DRC 到 MAVLink 语义复测",
    status: "needs_data",
    command: "python tools/person2_gap_test.py --phase drc-sitl --host 192.168.2.188 --port 1883 --sn EDGEGW001 --drone-sn DRONESIM001",
    expect: "DRC enter/heartbeat/速度/takeoff_to_point/stop 都可用，并产生指点飞行进度事件。",
    evidence: "drc/up、services_reply、takeoff_to_point_progress 或 fly_to_point_progress、SITL OSD。",
  },
  {
    id: "nest-gateway-live",
    category: "机场",
    title: "机场机构网关联动",
    status: "needs_data",
    command: "python tools/person2_gap_test.py --phase nest-gateway --host 192.168.2.188 --port 1883 --sn EDGEGW001 --drone-sn DRONESIM001",
    expect: "debug/cover/charge/putter/HMS 经网关下发，并观察到 dock_operation 或 HMS 事件。",
    evidence: "网关日志、机巢模拟器日志、HMS 事件、dock_operation 事件。",
  },
  {
    id: "retention-records",
    category: "日志",
    title: "飞行过程日志留证",
    status: "needs_data",
    command: "python tools/person2_gap_test.py --phase retention --host 192.168.2.188 --port 1883 --sn EDGEGW001 --drone-sn DRONESIM001",
    expect: "HMS/log/任务进度入口可查，页面时间线能清楚展示电量、高度、经纬度和回包。",
    evidence: "数据库记录、request trace、HMS/log 事件、清理前后计数。",
  },
  {
    id: "non-blocking-person2",
    category: "稳定性",
    title: "MAVLink/状态机非阻塞探针",
    status: "needs_data",
    command: "python tools/person2_gap_test.py --phase non-blocking --host 192.168.2.188 --port 1883 --sn EDGEGW001 --drone-sn DRONESIM001 --loop-count 8",
    expect: "连续 property_get 延迟不超过阈值，期间飞行器 OSD 不断流。",
    evidence: "每轮 elapsed_ms、OSD 观察、request trace 耗时。",
  },
];

const actionGroups = {
  dock: [
    action("核心属性查询", "读取飞行模式、DRC、舱盖、推杆、充电和补光灯状态。", "property_get", {properties: ["mode_code", "gear", "drc_state", "cover_state", "putter_state", "charging_state", "supplement_light_state"]}),
    action("进入远程调试", "通过 APIDock3 services 打开远程调试。", "debug_mode_open", {}),
    action("退出远程调试", "关闭调试模式，恢复普通业务状态。", "debug_mode_close", {}),
    action("打开舱盖", "下发 cover_open。", "cover_open", {}),
    action("关闭舱盖", "下发 cover_close。", "cover_close", {}),
    action("关闭充电", "断开充电，为起飞释放做准备。", "charge_close", {}),
    action("打开推杆", "释放飞行器固定机构。", "putter_open", {}),
    action("关闭推杆", "收回飞行器固定机构。", "putter_close", {}),
    action("打开补光灯", "打开机场补光灯。", "supplement_light_open", {}),
    action("关闭补光灯", "关闭机场补光灯。", "supplement_light_close", {}),
  ],
  mission: [
    action("解锁 QLAN 模式", "使用 set_flight_mode，按 QLAN 别名切到 QSTABILIZE 并 arm；避免 QLAND 不可解锁。", "set_flight_mode", {mode: "QLAN", arm: true, timeout: 10}),
    action("准备真实 HD 航线", "上传 HD.original.waypoints 到真实 SITL 飞控；需要网关已连接 127.0.0.1:14550。", "flighttask_prepare", realHdMissionData()),
    action("执行航线", "触发 flighttask_execute。", "flighttask_execute", {flight_id: "demo-flight-001"}),
    action("查询任务进度", "查询当前 flighttask_progress。", "flighttask_progress", {flight_id: "demo-flight-001"}),
    action("暂停航线", "触发 flighttask_pause。", "flighttask_pause", {flight_id: "demo-flight-001"}),
    action("恢复航线", "触发 flighttask_resume。", "flighttask_resume", {flight_id: "demo-flight-001"}),
    action("任务返航", "触发 flighttask_return。", "flighttask_return", {flight_id: "demo-flight-001"}),
    action("终止任务", "触发 flighttask_undo。", "flighttask_undo", {flight_id: "demo-flight-001"}),
    action("取消返航", "触发 return_home_cancel。", "return_home_cancel", {}),
  ],
  drc: [
    action("进入 DRC", "申请云端控制链路。", "drc_mode_enter", buildDrcEnterData),
    action("发送心跳", "向 drc/down 发送 heart_beat。", "heart_beat", {}, "drc"),
    action("速度推送", "发送一帧低速杆量/速度数据。", "drc_speed_push", {x: 0, y: 0, z: 0, yaw: 0}, "drc"),
    action("退出 DRC", "释放控制权。", "drc_mode_exit", {}),
  ],
  ops: [
    action("HMS 查询", "查询 HMS 告警/健康状态。", "hms_get", {page: 1, page_size: 20}),
    action("清空 HMS", "清空当前模拟 HMS 告警。", "hms_clear", {}),
    action("日志列表", "查询可上传日志文件。", "device_log_list_get", {page: 1, page_size: 20}),
    action("飞行状态属性", "读取 mode_code、gear、drc_state 等飞行过程关键状态。", "property_get", {properties: ["mode_code", "gear", "drc_state", "home_distance", "height"]}),
  ],
};

const quickActionRefs = [
  ["dock", 0],
  ["dock", 1],
  ["mission", 0],
  ["mission", 1],
  ["drc", 0],
  ["ops", 0],
];

function action(title, desc, method, data, channel = "services") {
  return {title, desc, method, data, channel};
}

function realHdMissionData() {
  return {
    flight_id: "demo-flight-001",
    execute_time: Date.now(),
    task_type: 0,
    file: {
      path: "/home/fenghuolun/lxz/edge_gateway/tools/ardupilot_sitl/HD.original.waypoints",
      fingerprint: "hd-original-qgc-wpl",
      name: "HD.original.waypoints",
    },
    simulate_mission: {
      is_enable: 0,
      latitude: 44.0474824,
      longitude: 125.3614503,
    },
  };
}

function buildDrcEnterData() {
  const mqtt = snapshot?.mqtt || {};
  return {
    mqtt_broker: {
      address: mqtt.host || "192.168.2.188",
      port: mqtt.port || 1883,
    },
    osd_frequency: 10,
    hsi_frequency: 1,
  };
}

function fmtJson(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

function shortTime(ts) {
  if (!ts) return "--";
  return new Date(ts).toLocaleTimeString();
}

function valueOfField(obj, key) {
  const item = obj?.data?.[key] ?? obj?.[key];
  if (item && typeof item === "object" && "value" in item) return item.value;
  return item;
}

function pickValue(obj, paths) {
  for (const path of paths) {
    let current = obj;
    for (const part of path.split(".")) {
      current = valueOfField(current, part);
      if (current === undefined || current === null || current === "") break;
    }
    if (current !== undefined && current !== null && current !== "") return current;
  }
  return undefined;
}

function numberText(value, digits = 6) {
  if (value === undefined || value === null || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toFixed(digits);
}

function telemetryFromOsd(osd) {
  const battery = pickValue(osd, ["battery.capacity_percent", "battery.remain_percent", "capacity_percent", "battery_percent"]);
  const height = pickValue(osd, ["height", "altitude", "relative_altitude"]);
  const lat = pickValue(osd, ["latitude", "lat", "position.latitude"]);
  const lng = pickValue(osd, ["longitude", "lng", "lon", "position.longitude"]);
  const mode = pickValue(osd, ["mode_code", "flight_mode"]);
  const gear = pickValue(osd, ["gear"]);
  const gps = pickValue(osd, ["position_state.gps_number", "gps_number", "satellites_visible"]);
  const quality = pickValue(osd, ["position_state.quality", "gps_quality"]);
  return {battery, height, lat, lng, mode, gear, gps, quality};
}

function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text;
}

function stateText(value, map) {
  if (value === undefined || value === null || value === "") return "--";
  return map?.[String(value)] ?? String(value);
}

function updateOverview(data) {
  snapshot = data;
  const dock = data.latest?.dock_osd || {};
  const drone = data.latest?.drone_osd || {};
  const dockSeen = Boolean(Object.keys(dock).length);
  const droneSeen = Boolean(Object.keys(drone).length);

  setText("gatewaySn", data.gateway_sn || "--");
  setText("droneSn", data.drone_sn || "--");
  setText("messageCount", `${(data.messages || []).length} 条消息`);

  $("dockOnlineDot").className = `state-dot ${dockSeen ? "online" : ""}`;
  $("droneOnlineDot").className = `state-dot ${droneSeen ? "online" : ""}`;

  setText("dockStateLine", dockSeen ? "机场在线，OSD 持续上报" : "等待机场 OSD");
  setText("droneStateLine", droneSeen ? `${coordLine(drone)}` : "等待飞行器 OSD");
  setText("coverState", stateText(valueOfField(dock, "cover_state"), {"0": "关闭", "1": "打开"}));
  setText("putterState", stateText(valueOfField(dock, "putter_state"), {"0": "关闭", "1": "打开"}));
  setText("chargingState", stateText(valueOfField(dock, "charging_state"), {"0": "未充电", "1": "充电中"}));
  setText("dockWind", withUnit(valueOfField(dock, "wind_speed"), "m/s"));
  const telemetry = telemetryFromOsd(drone);
  setText("battery", withUnit(telemetry.battery, "%"));
  setText("height", withUnit(telemetry.height, "m"));
  setText("modeCode", stateText(telemetry.mode, {"0": "空闲", "17": "起飞", "26": "航线"}));
  setText("gpsQuality", gpsLine(drone));
  renderTelemetrySummary(drone, data.messages || []);

  updateMqttStatus(data.mqtt || {});
  renderMessages(data.messages || []);
  renderTimeline(data.messages || []);
  renderTelemetryTimeline(data.messages || []);
  renderKeyWatch(data.key_points || {}, data.key_info || []);
  renderLastResult();
}

function coordLine(drone) {
  const {lat, lng} = telemetryFromOsd(drone);
  if (lat === undefined || lng === undefined) return "飞行器在线，OSD 持续上报";
  return `${Number(lat).toFixed(5)}, ${Number(lng).toFixed(5)}`;
}

function gpsLine(drone) {
  const {quality, gps} = telemetryFromOsd(drone);
  if (quality === undefined && gps === undefined) return "--";
  return `Q${quality ?? "-"} / ${gps ?? "-"}星`;
}

function withUnit(value, unit) {
  if (value === undefined || value === null || value === "") return "--";
  return `${value}${unit}`;
}

function renderTelemetrySummary(drone, messages) {
  const telemetry = telemetryFromOsd(drone);
  const latestOsd = messages.find((item) => item.type === "osd" && item.topic?.includes(`/${snapshot?.drone_sn}/`));
  setText("telemetryUpdated", latestOsd ? `更新 ${shortTime(latestOsd.timestamp)}` : "等待 OSD");
  setText("telemetryBattery", withUnit(telemetry.battery, "%"));
  setText("telemetryHeight", withUnit(telemetry.height, "m"));
  setText("telemetryLng", numberText(telemetry.lng, 7));
  setText("telemetryLat", numberText(telemetry.lat, 7));
  setText("telemetryMode", `${stateText(telemetry.mode, {"0": "空闲", "17": "起飞", "26": "航线"})} / ${telemetry.gear ?? "--"}`);
  setText("telemetryGps", gpsLine(drone));
}

function updateMqttStatus(mqtt) {
  setText("mqttStatus", mqtt.connected ? "已连接" : "未连接");
  setText("mqttAddress", `${mqtt.host || "--"}:${mqtt.port || "--"}`);
  const box = document.querySelector(".side-status");
  box.className = `side-status ${mqtt.connected ? "ok" : "bad"}`;
}

function renderActionCards() {
  Object.entries(actionGroups).forEach(([group, actions]) => {
    document.querySelectorAll(`[data-actions="${group}"]`).forEach((container) => {
      container.innerHTML = actions.map((item, index) => actionCardHtml(item, `${group}:${index}`)).join("");
    });
  });

  $("quickActions").innerHTML = quickActionRefs
    .map(([group, index]) => actionCardHtml(actionGroups[group][index], `${group}:${index}`))
    .join("");

  document.querySelectorAll("[data-action-ref]").forEach((button) => {
    button.addEventListener("click", () => runActionRef(button.dataset.actionRef));
  });
}

function actionCardHtml(item, ref) {
  return `
    <article class="action-card">
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(item.desc)}</p>
      <footer>
        <span class="method-tag">${escapeHtml(item.method)}</span>
        <button data-action-ref="${escapeHtml(ref)}">执行</button>
      </footer>
    </article>
  `;
}

async function runActionRef(ref) {
  const [group, indexText] = ref.split(":");
  const item = actionGroups[group]?.[Number(indexText)];
  if (!item) return;
  await sendCommandPayload(item.channel, item.method, cloneData(item.data), item.title);
}

function cloneData(data) {
  if (typeof data === "function") return data();
  return JSON.parse(JSON.stringify(data ?? {}));
}

function renderWorkflows() {
  const list = $("workflowList");
  list.innerHTML = "";
  workflows.forEach((wf) => {
    const div = document.createElement("div");
    div.className = "workflow-card";
    const steps = (wf.steps || []).map((s) => s.label || s.method).slice(0, 4).join(" / ");
    div.innerHTML = `
      <h3>${escapeHtml(wf.name)}</h3>
      <p>${escapeHtml(steps)}${(wf.steps || []).length > 4 ? " ..." : ""}</p>
      <button data-workflow="${escapeHtml(wf.id)}">运行流程</button>
    `;
    div.querySelector("button").addEventListener("click", () => runWorkflow(wf.id, wf.name));
    list.appendChild(div);
  });
}

function renderLastResult() {
  const box = $("lastResult");
  if (!box) return;
  if (!lastCommandResult) {
    box.textContent = "还没有下发命令。";
    setText("lastResultStatus", "--");
    return;
  }
  const result = lastCommandResult.result || lastCommandResult;
  const status = result.status || lastCommandResult.status || "unknown";
  const method = result.method || result.request?.method || lastCommandResult.method || "-";
  const reply = result.reply || {};
  const code = reply?.data?.result ?? reply?.result ?? "-";
  setText("lastResultStatus", status);
  box.innerHTML = `
    <strong>${escapeHtml(lastCommandResult.title || method)}</strong><br>
    method: ${escapeHtml(method)}<br>
    状态: ${escapeHtml(status)}，结果码: ${escapeHtml(code)}<br>
    时间: ${escapeHtml(shortTime(result.timestamp || Date.now()))}
    <details>
      <summary>查看完整结果</summary>
      <pre>${escapeHtml(fmtJson(result))}</pre>
    </details>
  `;
}

function latestLogFor(itemId) {
  return retestLogs.find((log) => log.item_id === itemId || log.id === itemId);
}

function renderRetestList() {
  const list = $("retestList");
  if (!list) return;
  list.innerHTML = retestItems.map((item) => retestCardHtml(item)).join("");
  document.querySelectorAll("[data-retest-status]").forEach((button) => {
    button.addEventListener("click", () => saveRetestStatus(
      button.dataset.retestItem,
      button.dataset.retestStatus,
    ));
  });
  document.querySelectorAll("[data-retest-run]").forEach((button) => {
    button.addEventListener("click", () => runRetestItem(button.dataset.retestRun));
  });
  renderRetestSummary();
  renderRetestLogs();
}

function retestCardHtml(item) {
  const latest = latestLogFor(item.id);
  const status = latest?.status || item.status;
  const note = latest?.note || "";
  return `
    <article class="retest-card ${escapeHtml(status)}">
      <div class="retest-head">
        <span class="retest-category">${escapeHtml(item.category)}</span>
        <span class="retest-status">${escapeHtml(statusLabel(status))}</span>
      </div>
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(item.expect)}</p>
      <div class="retest-meta">
        <strong>命令/入口</strong>
        <code>${escapeHtml(item.command)}</code>
      </div>
      <div class="retest-meta">
        <strong>需要留证</strong>
        <span>${escapeHtml(item.evidence)}</span>
      </div>
      <textarea id="note-${escapeHtml(item.id)}" placeholder="测试备注：现象、tid、失败原因、日志位置">${escapeHtml(note)}</textarea>
      <button class="retest-run" data-retest-run="${escapeHtml(item.id)}">运行测试</button>
      <div class="retest-actions">
        <button data-retest-item="${escapeHtml(item.id)}" data-retest-status="pass">通过</button>
        <button data-retest-item="${escapeHtml(item.id)}" data-retest-status="fail">失败</button>
        <button data-retest-item="${escapeHtml(item.id)}" data-retest-status="needs_data">需真实数据</button>
      </div>
    </article>
  `;
}

function statusLabel(status) {
  return {
    pass: "通过",
    fail: "失败",
    needs_data: "需真实数据",
    blocked: "阻塞",
  }[status] || status || "未记录";
}

function selectorValue(value) {
  if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(value);
  return String(value).replace(/["\\]/g, "\\$&");
}

function renderRetestSummary() {
  const box = $("retestSummary");
  if (!box) return;
  const latestByItem = new Map();
  for (const log of retestLogs) {
    const id = log.item_id || log.id;
    if (id && !latestByItem.has(id)) latestByItem.set(id, log.status);
  }
  const counts = {pass: 0, fail: 0, needs_data: 0, unrecorded: 0};
  retestItems.forEach((item) => {
    const status = latestByItem.get(item.id) || item.status || "unrecorded";
    counts[status] = (counts[status] || 0) + 1;
  });
  box.innerHTML = `
    <div class="summary-tile"><span>通过</span><strong>${counts.pass || 0}</strong></div>
    <div class="summary-tile"><span>失败</span><strong>${counts.fail || 0}</strong></div>
    <div class="summary-tile"><span>需真实数据</span><strong>${counts.needs_data || 0}</strong></div>
    <div class="summary-tile"><span>未记录</span><strong>${counts.unrecorded || 0}</strong></div>
  `;
}

function renderRetestLogs() {
  const list = $("retestLogList");
  if (!list) return;
  setText("retestLogPath", snapshot?.retest_log_path || "demo_platform/test_logs/retest_log.jsonl");
  list.innerHTML = retestLogs.slice(0, 80).map((log) => `
    <div class="retest-log-item ${escapeHtml(log.status || "")}">
      <div class="message-head">
        <strong>${escapeHtml(log.category || log.source || "-")} · ${escapeHtml(log.title || "-")}</strong>
        <span>${escapeHtml(shortTime(log.timestamp))}</span>
      </div>
      <div>${escapeHtml(statusLabel(log.status))}${log.method ? ` · ${escapeHtml(log.method)}` : ""}</div>
      ${log.note ? `<p>${escapeHtml(log.note)}</p>` : ""}
      ${log.evidence && Object.keys(log.evidence).length ? `<details><summary>证据</summary><pre>${escapeHtml(fmtJson(log.evidence))}</pre></details>` : ""}
    </div>
  `).join("");
}

function renderKeyWatch(pointsMap, infoItems) {
  const grid = $("keyPointGrid");
  const list = $("keyInfoList");
  if (!grid || !list) return;

  const points = Object.values(pointsMap || {})
    .sort((a, b) => (keyStageOrder[a.stage] ?? 9) - (keyStageOrder[b.stage] ?? 9) || String(a.title).localeCompare(String(b.title), "zh-CN"));
  const captured = points.filter((point) => ["ok", "active"].includes(point.status)).length;
  setText("keyPointCount", `${captured}/${points.length} 已捕获`);
  grid.innerHTML = points.map((point) => {
    const status = point.status || "waiting";
    const latest = (point.evidence || [])[0] || {};
    const action = keyPointActions[point.id] || {label: "打开复测清单", type: "view", view: "retest"};
    return `
      <article class="key-point-card ${escapeHtml(status)}">
        <div class="key-point-head">
          <span>${escapeHtml(point.stage || "--")}</span>
          <b>${escapeHtml(keyStatusLabels[status] || status)}</b>
        </div>
        <strong>${escapeHtml(point.title || "--")}</strong>
        <p>${escapeHtml(point.summary || "--")}</p>
        <small>${escapeHtml(latest.source || "暂无来源")} · ${escapeHtml(point.last_seen ? shortTime(point.last_seen) : "--")}</small>
        <em>${escapeHtml(latest.detail || "等待 MQTT/OSD/事件命中该测试点")}</em>
        <div class="key-point-actions">
          <button data-key-point="${escapeHtml(point.id)}">${escapeHtml(action.label)}</button>
        </div>
      </article>
    `;
  }).join("");
  document.querySelectorAll("[data-key-point]").forEach((button) => {
    button.addEventListener("click", () => runKeyPointAction(button.dataset.keyPoint, button));
  });

  const items = infoItems || [];
  setText("keyInfoCount", `${items.length} 条`);
  if (!items.length) {
    list.innerHTML = '<div class="empty-state">等待 MQTT、OSD、State、服务回包或事件。</div>';
    return;
  }
  list.innerHTML = items.slice(0, 120).map((item) => `
    <div class="key-info-item ${escapeHtml(item.status || "active")}">
      <div class="key-info-head">
        <span>${escapeHtml(item.stage || "--")}</span>
        <strong>${escapeHtml(item.title || item.point_id || "--")}</strong>
        <time>${escapeHtml(shortTime(item.timestamp))}</time>
      </div>
      <p>${escapeHtml(item.summary || "--")}</p>
      <small>${escapeHtml(item.source || "--")} · ${escapeHtml(item.detail || "--")}</small>
    </div>
  `).join("");
}

async function runKeyPointAction(pointId, button) {
  const action = keyPointActions[pointId] || {type: "view", view: "retest"};
  if (action.type === "view") {
    switchView(action.view);
    return;
  }
  const originalText = button?.textContent || "";
  if (button) {
    button.disabled = true;
    button.textContent = "运行中...";
  }
  try {
    if (action.type === "retest") {
      await runRetestItem(action.item);
      switchView("retest");
    } else if (action.type === "action") {
      await runActionRef(action.ref);
    }
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalText;
    }
  }
}

async function loadRetestLogs() {
  try {
    const res = await fetch("/api/retest-log");
    const data = await res.json();
    retestLogs = data.logs || [];
    if (!snapshot) snapshot = {};
    snapshot.retest_log_path = data.path;
    renderRetestList();
  } catch (err) {
    retestLogs = [{
      timestamp: Date.now(),
      source: "page",
      category: "复测日志",
      title: "加载失败",
      status: "fail",
      note: err.message,
    }];
    renderRetestList();
  }
}

async function saveRetestStatus(itemId, status) {
  const item = retestItems.find((candidate) => candidate.id === itemId);
  if (!item) return;
  const note = $(`note-${item.id}`)?.value || "";
  const body = {
    id: item.id,
    item_id: item.id,
    source: "manual",
    category: item.category,
    title: item.title,
    status,
    note,
    method: item.command,
    evidence: {
      expect: item.expect,
      required_evidence: item.evidence,
      latest_command: lastCommandResult,
    },
  };
  const res = await fetch("/api/retest-log", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (data.ok) {
    retestLogs = [data.log, ...retestLogs.filter((log) => log.id !== data.log.id)];
    renderRetestList();
  }
}

async function runRetestItem(itemId) {
  const item = retestItems.find((candidate) => candidate.id === itemId);
  if (!item) return;
  const escapedItemId = selectorValue(itemId);
  const buttons = [...document.querySelectorAll(`[data-retest-run="${escapedItemId}"], [data-retest-item="${escapedItemId}"]`)];
  buttons.forEach((button) => button.disabled = true);
  const runButton = document.querySelector(`[data-retest-run="${escapedItemId}"]`);
  const originalText = runButton?.textContent || "运行测试";
  if (runButton) runButton.textContent = "运行中...";
  try {
    const res = await fetch(`/api/retest/${encodeURIComponent(itemId)}/run`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
    });
    const data = await res.json();
    if (data.ok) {
      retestLogs = [data.log, ...retestLogs.filter((log) => log.id !== data.log.id)];
      lastCommandResult = {title: item.title, method: item.command, result: data.log};
    } else {
      const failedLog = {
        id: item.id,
        item_id: item.id,
        timestamp: Date.now(),
        source: "retest_button",
        category: item.category,
        title: item.title,
        status: "fail",
        note: data.error || "run failed",
        method: item.command,
      };
      retestLogs = [failedLog, ...retestLogs.filter((log) => log.id !== failedLog.id)];
      lastCommandResult = {title: item.title, method: item.command, result: failedLog};
    }
    renderLastResult();
    renderRetestList();
  } catch (err) {
    const failedLog = {
      id: item.id,
      item_id: item.id,
      timestamp: Date.now(),
      source: "retest_button",
      category: item.category,
      title: item.title,
      status: "fail",
      note: err.message,
      method: item.command,
    };
    retestLogs = [failedLog, ...retestLogs.filter((log) => log.id !== failedLog.id)];
    lastCommandResult = {title: item.title, method: item.command, result: failedLog};
    renderLastResult();
    renderRetestList();
  } finally {
    buttons.forEach((button) => button.disabled = false);
    if (runButton) runButton.textContent = originalText;
  }
}

function renderMessages(messages) {
  const filter = $("rawFilter")?.value || "all";
  const list = $("messageList");
  if (!list) return;
  list.innerHTML = "";
  messages.filter((item) => matchRawFilter(item, filter)).slice(0, 60).forEach((item) => {
    const div = document.createElement("div");
    const replyTypes = new Set(["services_reply", "property_reply", "drc_reply", "request_reply"]);
    const cls = replyTypes.has(item.type) ? "reply" : item.type;
    const method = item.payload?.method || "-";
    div.className = `message-item ${cls}`;
    div.innerHTML = `
      <div class="message-head">
        <strong>${escapeHtml(item.type)} · ${escapeHtml(method)}</strong>
        <span>${shortTime(item.timestamp)}</span>
      </div>
      <div class="message-head">${escapeHtml(item.topic)}</div>
      <details>
        <summary>展开 payload</summary>
        <pre>${escapeHtml(fmtJson(item.payload))}</pre>
      </details>
    `;
    list.appendChild(div);
  });
}

function renderTimeline(messages) {
  const timeline = $("timeline");
  if (!timeline) return;
  const important = messages
    .filter((item) => ["event", "services_reply", "property_reply", "drc_reply", "status", "status_reply"].includes(item.type))
    .slice(0, 40);
  timeline.innerHTML = "";
  important.forEach((item) => {
    const method = item.payload?.method || item.payload?.data?.method || item.type;
    const code = item.payload?.data?.result ?? item.payload?.result;
    const div = document.createElement("div");
    div.className = "timeline-item";
    div.innerHTML = `
      <div class="timeline-time">${shortTime(item.timestamp)}</div>
      <div>
        <div class="timeline-title">${escapeHtml(method)}${code !== undefined ? ` · ${escapeHtml(code)}` : ""}</div>
        <div class="timeline-meta">${escapeHtml(item.topic)}</div>
      </div>
    `;
    timeline.appendChild(div);
  });
}

function renderTelemetryTimeline(messages) {
  const list = $("telemetryTimeline");
  if (!list) return;
  const droneSn = snapshot?.drone_sn || "";
  const rows = messages
    .filter((item) => item.type === "osd" && (!droneSn || item.topic?.includes(`/${droneSn}/`)))
    .slice(0, 80);
  setText("telemetryCount", `${rows.length} 条`);
  if (!rows.length) {
    list.innerHTML = '<div class="empty-state">等待飞行器 OSD。真实航线执行时这里会持续刷新电量、高度和经纬度。</div>';
    return;
  }
  list.innerHTML = rows.map((item) => {
    const telemetry = telemetryFromOsd(item.payload || {});
    return `
      <div class="telemetry-row">
        <time>${escapeHtml(shortTime(item.timestamp))}</time>
        <span>电量 <b>${escapeHtml(withUnit(telemetry.battery, "%"))}</b></span>
        <span>高度 <b>${escapeHtml(withUnit(telemetry.height, "m"))}</b></span>
        <span>纬度 <b>${escapeHtml(numberText(telemetry.lat, 7))}</b></span>
        <span>经度 <b>${escapeHtml(numberText(telemetry.lng, 7))}</b></span>
        <span>模式 <b>${escapeHtml(String(telemetry.mode ?? "--"))}</b></span>
        <span>GPS <b>${escapeHtml(gpsLine(item.payload || {}))}</b></span>
      </div>
    `;
  }).join("");
}

function matchRawFilter(item, filter) {
  if (filter === "all") return true;
  if (filter === "reply") return ["services_reply", "property_reply", "drc_reply", "request_reply"].includes(item.type);
  if (filter === "status") return ["status", "status_reply"].includes(item.type);
  return item.type === filter;
}

async function loadStatus() {
  const res = await fetch("/api/status");
  updateOverview(await res.json());
}

async function loadWorkflows() {
  const res = await fetch("/api/workflows");
  workflows = (await res.json()).workflows || [];
  renderWorkflows();
}

async function sendCommandPayload(channel, method, data, title) {
  const buttons = [...document.querySelectorAll("button")];
  buttons.forEach((button) => {
    if (button.dataset.actionRef || button.dataset.workflow || button.id === "sendBtn") button.disabled = true;
  });
  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({channel, method, data, timeout: 10}),
    });
    const result = await res.json();
    lastCommandResult = {title, method, result};
    $("commandResult").textContent = fmtJson(result);
    renderLastResult();
  } catch (err) {
    lastCommandResult = {title, method, status: "error", result: {error: err.message}};
    $("commandResult").textContent = err.message;
    renderLastResult();
  } finally {
    buttons.forEach((button) => button.disabled = false);
  }
}

async function sendManualCommand() {
  let data;
  try {
    data = JSON.parse($("payload").value || "{}");
  } catch (err) {
    $("commandResult").textContent = `JSON 解析失败: ${err.message}`;
    return;
  }
  await sendCommandPayload($("channel").value, $("method").value.trim(), data, "单条 Method");
}

async function runWorkflow(id, name) {
  const buttons = [...document.querySelectorAll("[data-workflow]")];
  buttons.forEach((button) => button.disabled = true);
  try {
    const res = await fetch(`/api/workflows/${encodeURIComponent(id)}/run`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({timeout: 10}),
    });
    const result = await res.json();
    lastCommandResult = {title: name, method: id, result};
    $("commandResult").textContent = fmtJson(result);
    renderLastResult();
  } catch (err) {
    lastCommandResult = {title: name, method: id, status: "error", result: {error: err.message}};
    $("commandResult").textContent = err.message;
    renderLastResult();
  } finally {
    buttons.forEach((button) => button.disabled = false);
  }
}

function connectStream() {
  const source = new EventSource("/api/stream");
  source.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.event === "snapshot") {
      updateOverview(data.payload);
    } else if (data.event === "mqtt_message" && snapshot) {
      snapshot.messages = [data.payload, ...(snapshot.messages || [])].slice(0, 260);
      const latest = snapshot.latest || {};
      if (data.payload.type === "osd") {
        if (data.payload.topic.includes(`/${snapshot.drone_sn}/`)) latest.drone_osd = data.payload.payload;
        else latest.dock_osd = data.payload.payload;
      }
      if (data.payload.type === "state") latest.state = data.payload.payload;
      if (data.payload.type === "event") latest.event = data.payload.payload;
      if (["services_reply", "property_reply", "drc_reply", "request_reply"].includes(data.payload.type)) latest.reply = data.payload.payload;
      if (["status", "status_reply"].includes(data.payload.type)) latest.status = data.payload.payload;
      snapshot.latest = latest;
      updateOverview(snapshot);
    } else if (data.event === "mqtt_status" && snapshot) {
      snapshot.mqtt = data.payload;
      updateOverview(snapshot);
    } else if (data.event === "key_update" && snapshot) {
      snapshot.key_points = data.payload.key_points || {};
      snapshot.key_info = data.payload.key_info || [];
      renderKeyWatch(snapshot.key_points, snapshot.key_info);
    } else if (data.event === "request") {
      lastCommandResult = {title: data.payload.method, method: data.payload.method, result: data.payload};
      renderLastResult();
    } else if (data.event === "retest_log") {
      retestLogs = [data.payload, ...retestLogs.filter((log) => log.id !== data.payload.id)];
      renderRetestList();
    }
  };
  source.onerror = () => setText("streamHint", "重连中");
  source.onopen = () => setText("streamHint", "实时");
}

function switchView(view) {
  activeView = view;
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((item) => {
    item.classList.toggle("active", item.id === `view-${view}`);
  });
  const [title, subtitle] = viewMeta[view] || viewMeta.dashboard;
  setText("viewTitle", title);
  setText("viewSubtitle", subtitle);
}

function openDrawer(open) {
  const drawer = $("manualDrawer");
  drawer.classList.toggle("open", open);
  drawer.setAttribute("aria-hidden", open ? "false" : "true");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});
$("sendBtn").addEventListener("click", sendManualCommand);
$("refreshBtn").addEventListener("click", () => {
  loadStatus();
  loadWorkflows();
});
$("openManualBtn").addEventListener("click", () => openDrawer(true));
$("closeManualBtn").addEventListener("click", () => openDrawer(false));
$("closeManualBackdrop").addEventListener("click", () => openDrawer(false));
$("rawFilter").addEventListener("change", () => renderMessages(snapshot?.messages || []));

renderActionCards();
loadStatus();
loadWorkflows();
loadRetestLogs();
connectStream();
