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

const viewMeta = {
  dashboard: ["设备总览", "查看机场、飞行器、链路和最近业务状态。"],
  dock: ["机场控制", "远程调试、舱盖、推杆、充电、补光灯和基础属性查询。"],
  mission: ["飞行任务", "航线准备、执行、暂停、恢复、返航和终止入口。"],
  drc: ["DRC 飞控", "进入控制、心跳、速度推送、退出控制。"],
  media: ["直播媒体", "直播能力、开始/停止直播、切换镜头、画质、媒体文件查询。"],
  ops: ["运维日志", "设备拓扑、状态、HMS、日志和配置类联调入口。"],
  retest: ["复测清单", "记录需要复测、需要真实 SITL/机巢/数据的点。"],
  raw: ["原始报文", "排障时查看完整 MQTT payload。"],
};

const retestItems = [
  {
    id: "one-key-mqtt",
    category: "一键启航",
    title: "一键启航 MQTT 实跑",
    status: "needs_data",
    command: "python tools/mqtt_one_key_takeoff_flow_test.py --host 192.168.2.188 --port 1883 --sn EDGEGW001 --simulate --timeout 60 --quiet-stale",
    expect: "one_key_takeoff_flow 返回 ok，出现 one_key_takeoff_progress 和 flighttask_progress。",
    evidence: "保存终端输出、services_reply、events 和对应 tid。",
  },
  {
    id: "sitl-wayline",
    category: "SITL",
    title: "真实 SITL 航点执行",
    status: "needs_data",
    command: "python tools/mqtt_dock3_wayline_test.py --host 192.168.2.188 --port 1883 --sn EDGEGW001 --timeout 20 --quiet-stale",
    expect: "真实 MAVLink Mission 上传后进入 AUTO/航线执行，progress 随航点或距离变化。",
    evidence: "SITL 日志、MQTT flighttask_progress、OSD 经纬度/高度变化。",
  },
  {
    id: "nest-v13-live",
    category: "机巢",
    title: "机巢 v1.3 离线协议专项",
    status: "needs_data",
    command: "python tools/nest_v13_protocol_test.py --verbose",
    expect: "12003/12004/12005、命令字、状态位和基础拒绝条件离线解析正确。",
    evidence: "脚本输出 nest v1.3 protocol test ok；这是协议基础，不等于网关实跑。",
  },
  {
    id: "nest-gateway-live",
    category: "机巢",
    title: "机巢 v1.3 网关联动",
    status: "needs_data",
    command: "python tools/person2_gap_test.py --phase nest-gateway --host 192.168.2.188 --port 1883 --sn EDGEGW001 --drone-sn DRONESIM001",
    expect: "debug/cover/charge/putter/HMS 经网关下发，并观察到 dock_operation 或 HMS 事件。",
    evidence: "网关日志、机巢模拟器日志、HMS 事件、dock_operation 事件。",
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
    expect: "DRC enter/heartbeat/速度/云台/takeoff_to_point/stop 都可用，并产生指点飞行进度事件。",
    evidence: "drc/up、services_reply、takeoff_to_point_progress 或 fly_to_point_progress、SITL OSD。",
  },
  {
    id: "non-blocking",
    category: "稳定性",
    title: "非阻塞主链路压测",
    status: "needs_data",
    command: "python tools/e2e_full_chain_test.py --host 192.168.2.188 --port 1883 --sn EDGEGW001 --timeout 12 --quiet-stale",
    expect: "MQTT 回调、MAVLink 接收、任务状态机不被 DB、文件、上传、日志、固件任务卡住。",
    evidence: "压测输出、request trace 耗时、无 heartbeat/OSD 长时间断流。",
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
  {
    id: "payload-camera-gimbal",
    category: "负载",
    title: "相机/云台基础控制",
    status: "needs_data",
    command: "python tools/person2_gap_test.py --phase payload --host 192.168.2.188 --port 1883 --sn EDGEGW001 --drone-sn DRONESIM001",
    expect: "相机能力、拍照、录像、云台状态、云台转动 method 有回包；无真实负载时记录 adapter/mock 边界。",
    evidence: "payload/camera/gimbal reply、云台角度状态、媒体文件或 mock adapter 日志。",
  },
  {
    id: "multi-device-offline",
    category: "设备",
    title: "多设备/解绑/离线/重连",
    status: "needs_data",
    command: "python tools/mqtt_dock3_device_test.py --host 192.168.2.188 --port 1883 --sn EDGEGW001 --timeout 20 --quiet-stale",
    expect: "多子设备拓扑、解绑、离线、重连不破坏 update_topo 和 binding 状态。",
    evidence: "status_reply、update_topo、organization requests_reply、设备状态日志。",
  },
  {
    id: "cmd-advanced",
    category: "CMD",
    title: "远程调试进阶失败码",
    status: "needs_data",
    command: "python tools/mqtt_dock3_cmd_test.py --host 192.168.2.188 --port 1883 --sn EDGEGW001 --timeout 20 --quiet-stale",
    expect: "真实耗时、失败码、安全拒绝、任务进度更贴近 Dock3。",
    evidence: "cmd reply、progress event、operation record、拒绝原因。",
  },
  {
    id: "retention-records",
    category: "运维",
    title: "操作记录和短周期数据保留",
    status: "needs_data",
    command: "python tools/person2_gap_test.py --phase retention --host 192.168.2.188 --port 1883 --sn EDGEGW001 --drone-sn DRONESIM001",
    expect: "HMS/log/任务进度入口可查，操作或日志事件可留证；DB 深度保留需要再查数据库。",
    evidence: "数据库记录、request trace、HMS/log 事件、清理前后计数。",
  },
  {
    id: "real-adapters",
    category: "真实 Adapter",
    title: "真实 OTA/OSS/直播推流 Adapter",
    status: "needs_data",
    command: "firmware/media/live 真实外部服务联调",
    expect: "真实 OTA 下载校验、OSS 上传、RTMP/WebRTC/GB28181 推流不只停留在 mock。",
    evidence: "外部服务 URL、上传/推流状态、失败重试和取消日志。",
  },
];

const actionGroups = {
  dock: [
    action("基础属性查询", "读取舱盖、推杆、充电、调试模式等关键状态。", "property_get", {properties: ["mode_code", "drc_state", "cover_state", "putter_state", "charging_state", "supplement_light_state"]}),
    action("进入远程调试", "打开调试模式，后续可执行机巢机构动作。", "debug_mode_open", {}),
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
    action("准备真实 HD 航线", "上传 HD.original.waypoints 到真实 SITL 飞控；需要网关已连接 127.0.0.1:14550。", "flighttask_prepare", realHdMissionData()),
    action("准备模拟航线", "准备一条 demo wayline，默认不依赖真实 KMZ。", "flighttask_prepare", demoMissionData()),
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
  media: [
    action("直播能力查询", "查询设备可用直播能力。", "live_capability_query", {}),
    action("开始直播", "以 mock 地址启动直播链路。", "live_start", {url: "mock://edge-gateway/live/main", video_id: "EDGEGW001/0/normal", quality: 1}),
    action("切换广角", "切换直播镜头到 wide。", "live_lens_change", {video_id: "EDGEGW001/0/normal", lens: "wide"}),
    action("设置流畅画质", "切换直播清晰度/码率。", "live_set_quality", {video_id: "EDGEGW001/0/normal", quality: 1}),
    action("停止直播", "停止当前直播。", "live_stop", {video_id: "EDGEGW001/0/normal"}),
    action("媒体文件列表", "查询本地媒体文件列表。", "file_list_get", {module: 0, offset: 0, limit: 20}),
    action("最高优先级媒体", "查询最高优先级上传任务媒体。", "highest_priority_upload_flighttask_media", {}),
  ],
  ops: [
    action("设备拓扑状态", "触发 status/topology 相关链路观察。", "airport_bind_status", {}),
    action("工作区查询", "查询 workspace_get。", "workspace_get", {}),
    action("能力查询", "查询设备能力集。", "device_capability_query", {}),
    action("HMS 查询", "查询 HMS 告警/健康状态。", "hms_get", {page: 1, page_size: 20}),
    action("清空 HMS", "清空当前模拟 HMS 告警。", "hms_clear", {}),
    action("日志列表", "查询可上传日志文件。", "device_log_list_get", {page: 1, page_size: 20}),
    action("日志上传", "触发一条模拟日志上传任务。", "device_log_upload", {logs: [{module: 0, log_id: "demo-log"}]}),
    action("固件属性", "读取 firmware_version / firmware_upgrade_status。", "property_get", {properties: ["firmware_version", "firmware_upgrade_status"]}),
  ],
};

const quickActionRefs = [
  ["dock", 0],
  ["dock", 1],
  ["mission", 0],
  ["mission", 1],
  ["drc", 0],
  ["media", 1],
];

function action(title, desc, method, data, channel = "services") {
  return {title, desc, method, data, channel};
}

function demoMissionData() {
  return {
    flight_id: "demo-flight-001",
    execute_time: Date.now(),
    task_type: 0,
    file: {
      url: "",
      fingerprint: "demo",
      name: "demo-wayline.kmz",
    },
    simulate_mission: {
      is_enable: 1,
      latitude: 39.9042,
      longitude: 116.397,
    },
  };
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
  setText("battery", withUnit(valueOfField(valueOfField(drone, "battery"), "capacity_percent"), "%"));
  setText("height", withUnit(valueOfField(drone, "height"), "m"));
  setText("modeCode", stateText(valueOfField(drone, "mode_code"), {"0": "空闲", "17": "起飞", "26": "航线"}));
  setText("gpsQuality", gpsLine(drone));

  updateMqttStatus(data.mqtt || {});
  renderMessages(data.messages || []);
  renderTimeline(data.messages || []);
  renderKeyWatch(data.key_points || {}, data.key_info || []);
  renderLastResult();
}

function coordLine(drone) {
  const lat = valueOfField(drone, "latitude");
  const lng = valueOfField(drone, "longitude");
  if (lat === undefined || lng === undefined) return "飞行器在线，OSD 持续上报";
  return `${Number(lat).toFixed(5)}, ${Number(lng).toFixed(5)}`;
}

function gpsLine(drone) {
  const pos = valueOfField(drone, "position_state") || {};
  const quality = valueOfField(pos, "quality");
  const gps = valueOfField(pos, "gps_number");
  if (quality === undefined && gps === undefined) return "--";
  return `Q${quality ?? "-"} / ${gps ?? "-"}星`;
}

function withUnit(value, unit) {
  if (value === undefined || value === null || value === "") return "--";
  return `${value}${unit}`;
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
      </article>
    `;
  }).join("");

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
