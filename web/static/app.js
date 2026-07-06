const state = {
  snapshot: null,
  filter: "all",
  messages: [],
  commands: [],
  heightHistory: [],
};

const labels = {
  osd: "遥测",
  state: "状态",
  event: "事件",
  service: "命令",
  service_reply: "回包",
  request: "请求",
  request_reply: "请求回包",
  status: "拓扑",
  other: "其他",
};

const $ = (id) => document.getElementById(id);

function fmtNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toFixed(digits);
}

function fmtTime(ts) {
  if (!ts) return "--";
  const date = new Date(Number(ts));
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function topicShort(topic) {
  return topic.replace("thing/product/EDGE-GW-001/", "").replace("sys/product/EDGE-GW-001/", "sys/");
}

function setPill(el, text, cls) {
  el.textContent = text;
  el.className = `status-pill ${cls || ""}`.trim();
}

function updateStatus(snapshot) {
  const mqtt = snapshot.mqtt || {};
  setPill($("mqttStatus"), mqtt.connected ? "MQTT 已连接" : "MQTT 未连接", mqtt.connected ? "is-ok" : "is-warn");

  const sitl = snapshot.sitl || {};
  setPill($("sitlStatus"), sitl.running ? `SITL 运行中 PID ${sitl.pid}` : "SITL 未托管", sitl.running ? "is-ok" : "");

  const mav = snapshot.mavlink || {};
  setPill($("mavlinkStatus"), `MAVLink ${mav.url || "待命"}`, "is-ok");
}

function updateTelemetry(osd) {
  const battery = osd.battery || {};
  const position = osd.position_state || {};

  $("heightValue").textContent = fmtNumber(osd.height, 2);
  $("verticalSpeedValue").textContent = fmtNumber(osd.vertical_speed, 2);
  $("horizontalSpeedValue").textContent = fmtNumber(osd.horizontal_speed, 2);
  $("batteryValue").textContent = battery.capacity_percent ?? "--";
  $("gpsValue").textContent = position.gps_number ?? "--";
  $("latValue").textContent = fmtNumber(osd.latitude, 7);
  $("lonValue").textContent = fmtNumber(osd.longitude, 7);
  $("altValue").textContent = `${fmtNumber(osd.altitude, 2)} m`;
  $("headValue").textContent = `${fmtNumber(osd.attitude_head, 1)}°`;
  $("modeValue").textContent = osd.mode_code ?? "--";
  $("gearValue").textContent = osd.gear === 1 ? "飞行中 / 已解锁" : osd.gear === 0 ? "地面 / 已上锁" : "--";
  $("lastUpdate").textContent = `最后更新 ${fmtTime(osd.timestamp || Date.now())}`;
}

function drawChart() {
  const canvas = $("heightChart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);

  ctx.fillStyle = "#101211";
  ctx.fillRect(0, 0, width, height);

  ctx.strokeStyle = "#27302b";
  ctx.lineWidth = 1;
  for (let i = 1; i < 5; i += 1) {
    const y = (height / 5) * i;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }

  const points = state.heightHistory
    .filter((item) => item && item.height !== null && item.height !== undefined)
    .slice(-120);

  if (points.length < 2) {
    ctx.fillStyle = "#6f7a70";
    ctx.font = "14px sans-serif";
    ctx.fillText("等待高度数据", 24, 34);
    return;
  }

  const values = points.map((p) => Number(p.height));
  const max = Math.max(1, ...values);
  const min = Math.min(0, ...values);
  const range = Math.max(1, max - min);

  ctx.strokeStyle = "#57d68d";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = (width / (points.length - 1)) * index;
    const y = height - ((Number(point.height) - min) / range) * (height - 28) - 14;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.fillStyle = "#a8b0a8";
  ctx.font = "12px sans-serif";
  ctx.fillText(`最高 ${fmtNumber(max, 1)}m`, 16, 20);
  ctx.fillText(`最低 ${fmtNumber(min, 1)}m`, 16, height - 12);
}

function summarizePayload(type, payload) {
  const rows = [];
  if (!payload || typeof payload !== "object") return rows;

  if (type === "osd") {
    rows.push(["高度", `${fmtNumber(payload.height, 2)} m`]);
    rows.push(["经纬度", `${fmtNumber(payload.latitude, 6)}, ${fmtNumber(payload.longitude, 6)}`]);
    rows.push(["垂直速度", `${fmtNumber(payload.vertical_speed, 2)} m/s`]);
    rows.push(["电量", `${payload.battery?.capacity_percent ?? "--"}%`]);
  } else if (type === "state") {
    const changes = payload.changes || payload;
    Object.entries(changes).slice(0, 5).forEach(([key, value]) => rows.push([key, String(value)]));
  } else if (type === "event") {
    rows.push(["方法", payload.method || "--"]);
    rows.push(["事务", payload.tid || "--"]);
    if (payload.data) rows.push(["数据", JSON.stringify(payload.data).slice(0, 90)]);
  } else if (type === "service_reply" || type === "request_reply") {
    rows.push(["方法", payload.method || "--"]);
    rows.push(["结果", payload.data?.result ?? "--"]);
    rows.push(["事务", payload.tid || "--"]);
  } else if (type === "service" || type === "request") {
    rows.push(["方法", payload.method || "--"]);
    rows.push(["需要回包", payload.need_reply === 1 ? "是" : "否"]);
    rows.push(["事务", payload.tid || "--"]);
  } else {
    Object.entries(payload).slice(0, 4).forEach(([key, value]) => rows.push([key, String(value).slice(0, 80)]));
  }
  return rows;
}

function renderMessages() {
  const list = $("messageList");
  const filtered = state.filter === "all"
    ? state.messages
    : state.messages.filter((item) => item.type === state.filter);

  $("messageCount").textContent = `${filtered.length} 条`;
  list.innerHTML = "";

  filtered.slice(0, 80).forEach((item) => {
    const card = document.createElement("article");
    card.className = "message-card";
    const rows = summarizePayload(item.type, item.payload);
    card.innerHTML = `
      <div class="message-head">
        <div>
          <span class="topic-type">${labels[item.type] || item.type}</span>
          <strong>${topicShort(item.topic)}</strong>
        </div>
        <span>${fmtTime(item.timestamp)}</span>
      </div>
      <div class="message-body">
        ${rows.map(([k, v]) => `<div class="field-row"><span>${k}</span><b>${escapeHtml(v)}</b></div>`).join("")}
        <button class="json-toggle">查看原始 JSON</button>
        <pre class="raw-json">${escapeHtml(JSON.stringify(item.payload, null, 2))}</pre>
      </div>
    `;
    card.querySelector(".json-toggle").addEventListener("click", () => card.classList.toggle("show-json"));
    list.appendChild(card);
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderCommands() {
  const list = $("commandLog");
  $("commandCount").textContent = `${state.commands.length} 条`;
  list.innerHTML = "";
  state.commands.slice(0, 20).forEach((item) => {
    const div = document.createElement("div");
    div.className = "mini-item";
    div.innerHTML = `<strong>${escapeHtml(item.label)} · ${escapeHtml(item.status)}</strong><span>${fmtTime(item.timestamp)} · ${escapeHtml(item.kind)}</span>`;
    list.appendChild(div);
  });
}

function renderSitlLogs(logs) {
  const target = $("sitlLogs");
  if (!logs || !logs.length) return;
  target.textContent = logs.slice(-120).map((item) => item.line).join("\n");
  target.scrollTop = target.scrollHeight;
}

function applySnapshot(snapshot) {
  state.snapshot = snapshot;
  state.messages = snapshot.messages || [];
  state.commands = snapshot.command_log || [];
  state.heightHistory = snapshot.osd_history || [];
  updateStatus(snapshot);
  if (snapshot.latest_osd) updateTelemetry(snapshot.latest_osd);
  renderMessages();
  renderCommands();
  renderSitlLogs(snapshot.sitl?.logs || []);
  drawChart();
}

async function postJson(url, payload = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || "请求失败");
  return data;
}

function bindControls() {
  document.querySelectorAll(".cmd[data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.action;
      const altitude = Number($("takeoffAlt").value || 10);
      button.disabled = true;
      try {
        await postJson("/api/mavlink", { action, altitude });
      } catch (err) {
        alert(`执行失败：${err.message}`);
      } finally {
        button.disabled = false;
      }
    });
  });

  $("startSitl").addEventListener("click", async () => {
    try {
      await postJson("/api/sitl/start", { vehicle: "ArduCopter" });
    } catch (err) {
      alert(`启动 SITL 失败：${err.message}`);
    }
  });

  $("stopSitl").addEventListener("click", async () => {
    try {
      await postJson("/api/sitl/stop");
    } catch (err) {
      alert(`停止 SITL 失败：${err.message}`);
    }
  });

  $("sendApi").addEventListener("click", async () => {
    let payload = {};
    try {
      payload = JSON.parse($("apiPayload").value || "{}");
    } catch (err) {
      alert("API 参数不是合法 JSON");
      return;
    }
    try {
      await postJson("/api/mqtt-command", { method: $("apiMethod").value, data: payload, channel: "services" });
    } catch (err) {
      alert(`发送 API 失败：${err.message}`);
    }
  });

  document.querySelectorAll(".filter").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".filter").forEach((node) => node.classList.remove("is-active"));
      button.classList.add("is-active");
      state.filter = button.dataset.filter || "all";
      renderMessages();
    });
  });
}

function connectStream() {
  const stream = new EventSource("/api/stream");
  stream.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.event === "snapshot") {
      applySnapshot(data.payload);
      return;
    }
    if (data.event === "mqtt_status" || data.event === "sitl_status") {
      fetch("/api/status").then((r) => r.json()).then(applySnapshot);
      return;
    }
    if (data.event === "mqtt_message") {
      const item = data.payload;
      state.messages.unshift(item);
      state.messages = state.messages.slice(0, 120);
      if (item.type === "osd") {
        updateTelemetry(item.payload);
        state.heightHistory.push({
          timestamp: item.payload.timestamp || item.timestamp,
          height: item.payload.height,
          altitude: item.payload.altitude,
          vertical_speed: item.payload.vertical_speed,
          battery: item.payload.battery?.capacity_percent,
        });
        state.heightHistory = state.heightHistory.slice(-180);
        drawChart();
      }
      renderMessages();
      return;
    }
    if (data.event === "command") {
      state.commands.unshift(data.payload);
      state.commands = state.commands.slice(0, 80);
      renderCommands();
      return;
    }
    if (data.event === "sitl_log") {
      const current = $("sitlLogs").textContent;
      $("sitlLogs").textContent = `${current}\n${data.payload.line}`.split("\n").slice(-120).join("\n");
      $("sitlLogs").scrollTop = $("sitlLogs").scrollHeight;
    }
  };
}

async function boot() {
  bindControls();
  const snapshot = await fetch("/api/status").then((res) => res.json());
  applySnapshot(snapshot);
  connectStream();
}

boot().catch((err) => {
  console.error(err);
  alert(`页面初始化失败：${err.message}`);
});
