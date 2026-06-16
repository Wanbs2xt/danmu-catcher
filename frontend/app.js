const state = {
  rooms: [],
  danmu: [],
  winners: [],
  settings: {},
  statusFilter: "all",
  integrations: {},
  authSessionId: "",
  authPopup: null,
  authPollTimer: null,
  authStartedAt: 0,
  authConnecting: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "请求失败");
  }
  return payload;
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => el.classList.remove("show"), 2200);
}

function setAuthVisual(status, title, hint) {
  const box = $("#roomAuthState");
  box.classList.remove("idle", "waiting", "success");
  box.classList.add(status);
  $("#roomAuthTitle").textContent = title;
  $("#roomAuthHint").textContent = hint;
}

function roomKey(room) {
  return `${room.sourceType || "web"}::${room.controlUrl || room.url || room.id}`;
}

function uniqueRooms(rooms) {
  const seen = new Set();
  return rooms.filter((room) => {
    const key = roomKey(room);
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function upsertRoom(room) {
  const key = roomKey(room);
  const index = state.rooms.findIndex((item) => roomKey(item) === key || item.id === room.id);
  if (index >= 0) {
    state.rooms[index] = room;
  } else {
    state.rooms.push(room);
  }
  state.rooms = uniqueRooms(state.rooms);
}

function syncSettingsFromForm() {
  const keywords = $("#keywordInput").value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return {
    serialMode: document.querySelector("input[name='serialRule']:checked").value,
    serialStart: Number($("#serialStart").value || 1),
    serialEnd: Number($("#serialEnd").value || 999999),
    includeDecimal: $("#includeDecimal").checked,
    formatRules: $("#pureNumberRule").checked ? ["pureNumber"] : [],
    keywords: $("#keywordEnabled").checked ? keywords : [],
    limitEnabled: $("#limitEnabled").checked,
    limitCount: Number($("#limitCount").value || 100),
    fastPassEnabled: $("#fastPassEnabled").checked,
    fastPassSeconds: Number($("#fastPassSeconds").value || 30),
    dedupeEnabled: $("#dedupeEnabled").checked,
    dedupeSeconds: Number($("#dedupeSeconds").value || 5),
    lampPriority: $("#lampPriority").checked,
    emptyPrintEnabled: $("#emptyPrintEnabled").checked,
    template: $("#templateSelect").value,
    printer: $("#printerSelect").value,
  };
}

async function persistSettings() {
  state.settings = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify(syncSettingsFromForm()),
  });
  renderStats();
}

function applySettings(settings) {
  state.settings = settings;
  $("#toggleCaptureBtn").textContent = settings.running ? "停止自动打印" : "开启自动打印";
  $("#toggleCaptureBtn").classList.toggle("running", Boolean(settings.running));
  $("#serialStart").value = settings.serialStart ?? 1;
  $("#serialEnd").value = settings.serialEnd ?? 999999;
  $("#includeDecimal").checked = Boolean(settings.includeDecimal);
  $("#pureNumberRule").checked = (settings.formatRules || []).includes("pureNumber");
  $("#keywordEnabled").checked = Boolean((settings.keywords || []).length);
  $("#keywordInput").value = (settings.keywords || []).join(",");
  $("#limitEnabled").checked = Boolean(settings.limitEnabled);
  $("#limitCount").value = settings.limitCount ?? 100;
  $("#fastPassEnabled").checked = Boolean(settings.fastPassEnabled);
  $("#fastPassSeconds").value = settings.fastPassSeconds ?? 30;
  $("#dedupeEnabled").checked = Boolean(settings.dedupeEnabled);
  $("#dedupeSeconds").value = settings.dedupeSeconds ?? 5;
  $("#lampPriority").checked = Boolean(settings.lampPriority);
  $("#emptyPrintEnabled").checked = Boolean(settings.emptyPrintEnabled);
  $("#templateSelect").value = settings.template || "标签纸60x40";
  $("#printerSelect").value = settings.printer || "HPRT N31D";
  renderStats();
}

function renderRooms() {
  const boundRooms = state.rooms.filter((room) => room.captureStatus === "已绑定" || room.sourceType !== "studio");
  const options = boundRooms
    .map((room) => {
      const sourceLabel = room.sourceType === "studio" ? "中控台" : "网页";
      return `<option value="${room.id}">${room.name} (${sourceLabel})</option>`;
    })
    .join("");
  $("#roomSelect").innerHTML = options || "<option>暂无直播间</option>";
  $("#filterRoom").innerHTML = `<option value="all">全部直播间</option>${options}`;
}

function renderStats() {
  const matchedCount = state.danmu.filter((item) => item.matchedContent).length;
  $("#localCount").textContent = matchedCount;
  $("#limitStat").textContent = state.settings.limitCount || 100;
}

function renderDanmu(rows = state.danmu) {
  const body = $("#danmuBody");
  $("#emptyState").style.display = rows.length ? "none" : "grid";
  body.innerHTML = rows
    .map((item, index) => {
      const status = item.matchedContent ? "已扣中" : "未扣中";
      return `
        <tr>
          <td>${index + 1}</td>
          <td>${item.roomName || ""}</td>
          <td>${item.userName || ""}</td>
          <td title="${item.content || ""}">${item.content || ""}</td>
          <td>${item.matchedContent || "-"}</td>
          <td>${item.batchNo || "-"}</td>
          <td>${item.publicTime || ""}</td>
          <td><span class="status-pill">${status}</span></td>
          <td><button class="link-button" data-print="${item.id}">补打</button></td>
        </tr>
      `;
    })
    .join("");
  renderStats();
}

async function loadState() {
  const payload = await api("/api/state");
  state.rooms = uniqueRooms(payload.rooms || []);
  state.danmu = payload.danmu;
  state.winners = payload.winners;
  state.integrations = payload.integrations || {};
  renderRooms();
  applySettings(payload.settings);
  renderDanmu();
}

async function queryDanmu() {
  const params = new URLSearchParams({
    status: state.statusFilter,
    keyword: $("#searchInput").value.trim(),
    room: $("#filterRoom").value,
  });
  const rows = await api(`/api/danmu?${params.toString()}`);
  renderDanmu(rows);
}

async function loadStudioBindingInfo() {
  const payload = await api("/api/integrations/douyin-studio");
  setAuthVisual("idle", "等待登录", "进入直播中控台后，这里会在几秒内自动尝试连接直播中控台。");
  $("#saveRoomBtn").disabled = true;
  $("#saveRoomBtn").textContent = "自动等待登录完成";
  return payload;
}

async function finishStudioLogin() {
  if (!state.authSessionId || state.authConnecting) {
    return;
  }
  state.authConnecting = true;
  setAuthVisual("waiting", "正在连接中控台", "正在同步登录状态并准备开始抓取弹幕。");
  try {
    const result = await api("/api/integrations/douyin-studio/auth/complete", {
      method: "POST",
      body: JSON.stringify({
        sessionId: state.authSessionId,
      }),
    });
    upsertRoom(result.room);
    state.settings = result.settings;
    renderRooms();
    applySettings(result.settings);
    $("#saveRoomBtn").disabled = false;
    $("#saveRoomBtn").textContent = "连接成功";
    setAuthVisual("success", "连接成功", "已进入直播中控台，弹幕抓取已自动开始。");
    if (state.authPopup && !state.authPopup.closed) {
      try {
        state.authPopup.close();
      } catch (error) {
        console.debug("popup close skipped", error);
      }
    }
    toast("直播中控台已连接，开始抓取弹幕");
    window.setTimeout(() => {
      $("#roomDialog").close();
      resetRoomDialog();
    }, 900);
  } finally {
    state.authConnecting = false;
  }
}

function resetRoomDialog() {
  $("#saveRoomBtn").disabled = true;
  $("#saveRoomBtn").textContent = "自动等待登录完成";
  setAuthVisual("idle", "等待登录", "进入直播中控台后，这里会在几秒内自动尝试连接直播中控台。");
  state.authSessionId = "";
  state.authPopup = null;
  state.authStartedAt = 0;
  state.authConnecting = false;
  if (state.authPollTimer) {
    window.clearInterval(state.authPollTimer);
    state.authPollTimer = null;
  }
}

function watchAuthPopup() {
  if (state.authPollTimer) {
    window.clearInterval(state.authPollTimer);
  }
  state.authPollTimer = window.setInterval(async () => {
    const elapsed = Date.now() - state.authStartedAt;
    const popupStillOpen = state.authPopup && !state.authPopup.closed;
    const shouldAutoContinue = popupStillOpen && elapsed >= 3500;
    const shouldContinueAfterClose = !popupStillOpen;

    if (!shouldAutoContinue && !shouldContinueAfterClose) {
      return;
    }

    window.clearInterval(state.authPollTimer);
    state.authPollTimer = null;
    try {
      await finishStudioLogin();
    } catch (error) {
      setAuthVisual("idle", "连接失败", error.message || "登录已结束，但连接直播中控台失败。");
      toast(error.message || "连接直播中控台失败");
    }
  }, 1200);
}

async function tryAutoConnectOnFocus() {
  if (!state.authSessionId || state.authConnecting) {
    return;
  }
  const elapsed = Date.now() - state.authStartedAt;
  if (elapsed < 2000) {
    return;
  }
  try {
    await finishStudioLogin();
  } catch (error) {
    setAuthVisual("waiting", "等待登录完成", "已回到主窗口，继续等待中控台登录稳定后自动连接。");
  }
}

function setupEvents() {
  $("#addRoomBtn").addEventListener("click", async () => {
    await loadStudioBindingInfo();
    $("#roomDialog").showModal();
  });
  $("#openStudioLoginBtn").addEventListener("click", async () => {
    const session = await api("/api/integrations/douyin-studio/auth/start", {
      method: "POST",
      body: "{}",
    });
    state.authSessionId = session.id;
    const url = session.authUrl || "";
    if (!url) {
      toast("登录地址还没准备好");
      return;
    }
    state.authPopup = window.open(url, "_blank", "width=1180,height=820");
    state.authStartedAt = Date.now();
    if (!state.authPopup) {
      setAuthVisual("waiting", "已打开中控台", "浏览器未返回窗口句柄。进入直播中控台后，这里会自动继续连接。");
      toast("已打开中控台，登录完成后会自动连接");
    } else {
      setAuthVisual("waiting", "等待登录完成", "请在新窗口完成抖店登录或进入直播中控台，这里会在几秒后自动继续连接。");
      watchAuthPopup();
    }
    $("#saveRoomBtn").disabled = true;
  });
  $("#saveRoomBtn").addEventListener("click", async (event) => {
    event.preventDefault();
  });
  $("#cancelRoomBtn").addEventListener("click", () => resetRoomDialog());

  $("#toggleCaptureBtn").addEventListener("click", async () => {
    await persistSettings();
    const path = state.settings.running ? "/api/capture/stop" : "/api/capture/start";
    applySettings(await api(path, { method: "POST", body: "{}" }));
    toast(state.settings.running ? "已开启自动打印" : "已停止自动打印");
  });

  $("#drawBtn").addEventListener("click", async () => {
    await persistSettings();
    const winner = await api("/api/draw", { method: "POST", body: "{}" });
    toast(`已抽中 ${winner.userName}，批次号 ${winner.batchNo}`);
    await loadState();
  });

  $("#testPrintBtn").addEventListener("click", async () => {
    await persistSettings();
    await api("/api/print", {
      method: "POST",
      body: JSON.stringify({
        id: `manual_${Date.now()}`,
        userName: "测试买家",
        batchNo: "TEST-0001",
      }),
    });
    toast("测试打印已写入打印日志");
  });

  $$(".segmented button").forEach((button) => {
    button.addEventListener("click", () => {
      $$(".segmented button").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.statusFilter = button.dataset.status;
      queryDanmu().catch((error) => toast(error.message));
    });
  });

  $("#searchBtn").addEventListener("click", () => queryDanmu().catch((error) => toast(error.message)));
  $("#resetBtn").addEventListener("click", () => {
    $("#searchInput").value = "";
    $("#filterRoom").value = "all";
    state.statusFilter = "all";
    $$(".segmented button").forEach((item) => item.classList.toggle("active", item.dataset.status === "all"));
    renderDanmu();
  });
  $("#exportBtn").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(state.danmu, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `danmu-${Date.now()}.json`;
    link.click();
    URL.revokeObjectURL(url);
  });

  [
    "#serialStart",
    "#serialEnd",
    "#includeDecimal",
    "#pureNumberRule",
    "#keywordEnabled",
    "#keywordInput",
    "#limitEnabled",
    "#limitCount",
    "#fastPassEnabled",
    "#fastPassSeconds",
    "#dedupeEnabled",
    "#dedupeSeconds",
    "#lampPriority",
    "#emptyPrintEnabled",
    "#templateSelect",
    "#printerSelect",
  ].forEach((selector) => {
    $(selector).addEventListener("change", () => persistSettings().catch((error) => toast(error.message)));
  });
}

function connectEvents() {
  const source = new EventSource("/events");
  source.addEventListener("danmu", (event) => {
    const item = JSON.parse(event.data);
    state.danmu.unshift(item);
    state.danmu = state.danmu.slice(0, 500);
    renderDanmu();
  });
  source.addEventListener("settings", (event) => applySettings(JSON.parse(event.data)));
  source.addEventListener("winner", (event) => {
    const winner = JSON.parse(event.data);
    state.winners.unshift(winner);
  });
  source.addEventListener("room", (event) => {
    const room = JSON.parse(event.data);
    upsertRoom(room);
    renderRooms();
  });
  source.onerror = () => {
    toast("实时连接重试中");
  };
}

window.addEventListener("DOMContentLoaded", async () => {
  setupEvents();
  resetRoomDialog();
  window.addEventListener("focus", () => {
    tryAutoConnectOnFocus().catch((error) => console.debug("focus auto connect skipped", error));
  });
  try {
    await loadState();
    connectEvents();
  } catch (error) {
    toast(error.message);
  }
});
