const { createApp, computed, onMounted, reactive, ref } = Vue;

createApp({
  setup() {
    const status = reactive({
      cameras: [],
    });
    const system = reactive({
      cpu: { cores: [] },
      memory: {},
      temperatures: [],
      npu: { cores: [] },
      mpp: { decoder: [], encoder: [] },
      rga: { debug: { schedulers: [] }, domains: [], clocks: [] },
    });
    const form = reactive({
      contexts: 8,
      dryRun: false,
    });
    const newCamera = reactive({
      id: "",
      name: "",
      source_url: "",
      width: 640,
      height: 360,
      contexts: 8,
      decoder: "software",
    });
    const wsConnected = ref(false);
    const message = ref("");
    const logs = ref([]);
    const alarms = ref([]);
    const alarmSoundEnabled = ref(localStorage.getItem("alarmSoundEnabled") === "true");
    const alarmSettings = reactive({
      confidenceThresholdPercent: 50,
      recordingEnabled: true,
      recordingDirectory: "",
    });
    const activeTab = ref("cameras");
    const monitorSize = ref(localStorage.getItem("monitorSize") || "medium");
    const monitorSizeOptions = [
      { value: "small", label: "小" },
      { value: "medium", label: "中" },
      { value: "large", label: "大" },
      { value: "full", label: "满宽" },
    ];
    const chartTooltip = reactive({
      visible: false,
      x: 0,
      y: 0,
      text: "",
    });
    const metricHistory = reactive({
      labels: [],
      cpu: [],
      memory: [],
      npu: [],
      rga: [],
      temp: [],
    });
    const cameraFormCache = {};
    const cameraEditCache = {};
    const cameraDetectionCache = {};
    const cameraPreviewCache = {};
    const cameraPreviewPending = {};
    const cameraRunCache = {};

    const totalRunning = computed(() => status.cameras.filter((camera) => camera.running).length);
    const totalStreaming = computed(() => status.cameras.filter((camera) => camera.streaming).length);
    const unacknowledgedAlarms = computed(() => alarms.value.filter((event) => !event.acknowledged));
    const activeAlarm = computed(() => unacknowledgedAlarms.value[0] || null);

    function normalizePlayerUrl(url) {
      if (!url) return "";
      if (url.startsWith("http://") || url.startsWith("https://")) return url;
      return `${location.protocol}//${location.hostname}:8889${url}`;
    }

    function formForCamera(cameraId) {
      if (!cameraFormCache[cameraId]) {
        cameraFormCache[cameraId] = reactive({
          source: "",
          contexts: form.contexts,
          dryRun: form.dryRun,
        });
      }
      return cameraFormCache[cameraId];
    }

    function editFormForCamera(camera) {
      if (!cameraEditCache[camera.id]) {
        cameraEditCache[camera.id] = reactive({
          name: camera.name || camera.id,
          source_url: camera.source_url || "",
          width: Number(camera.width) || 640,
          height: Number(camera.height) || 360,
          contexts: Number(camera.configured_contexts) || 1,
          decoder: camera.decoder || "software",
        });
      }
      return cameraEditCache[camera.id];
    }

    function appendLog(text, cameraId = "") {
      const now = new Date().toLocaleTimeString();
      const prefix = cameraId ? `[${cameraId}] ` : "";
      logs.value.unshift(`[${now}] ${prefix}${text}`);
      logs.value = logs.value.slice(0, 160);
    }

    function applyStatus(payload) {
      const cameras = Array.isArray(payload.cameras) ? payload.cameras : [payload].filter(Boolean);
      status.cameras = cameras.map((camera) => {
        const runId = Number(camera.started_at || 0);
        if (cameraRunCache[camera.id] !== undefined && cameraRunCache[camera.id] !== runId) {
          delete cameraDetectionCache[camera.id];
          delete cameraPreviewCache[camera.id];
          delete cameraPreviewPending[camera.id];
        }
        cameraRunCache[camera.id] = runId;
        const hasSynchronizedPreview = Boolean(
          cameraDetectionCache[camera.id] && cameraPreviewCache[camera.id]
        );
        return {
          ...camera,
          // API status omits the paired JPEG. Never draw that unsynchronized
          // result over the fallback WebRTC player.
          last_result: hasSynchronizedPreview ? cameraDetectionCache[camera.id] : null,
          preview_url: hasSynchronizedPreview ? cameraPreviewCache[camera.id] : "",
          form: formForCamera(camera.id),
          edit: editFormForCamera(camera),
          player_url_abs: normalizePlayerUrl(camera.player_url),
          rtsp_url_abs: camera.rtsp_url || `rtsp://${location.hostname}:8554/live/${camera.id}`,
          hls_url_abs: camera.hls_url && camera.hls_url.startsWith("http")
            ? camera.hls_url
            : `${location.protocol}//${location.hostname}:8888${camera.hls_url || `/live/${camera.id}/index.m3u8`}`,
        };
      });
    }

    function updateCameraDetection(cameraId, payload) {
      const camera = status.cameras.find((item) => item.id === cameraId);
      if (!camera) return;
      if (payload.preview_jpeg) {
        const displayPayload = { ...payload };
        delete displayPayload.preview_jpeg;
        const previewUrl = `data:image/jpeg;base64,${payload.preview_jpeg}`;
        const frameId = Number(payload.frame_id || 0);
        const frameToken = `${Number(camera.started_at || 0)}:${frameId}:${payload.timestamp || 0}`;
        cameraPreviewPending[cameraId] = frameToken;
        const previewImage = new Image();
        previewImage.onload = () => {
          // Decoding an <img> is asynchronous. Commit the decoded image and
          // its detections together, and discard an older decode that finishes
          // after a newer frame has arrived.
          if (cameraPreviewPending[cameraId] !== frameToken) return;
          const currentCamera = status.cameras.find((item) => item.id === cameraId);
          if (!currentCamera) return;
          cameraDetectionCache[cameraId] = displayPayload;
          cameraPreviewCache[cameraId] = previewUrl;
          currentCamera.last_result = displayPayload;
          currentCamera.preview_url = previewUrl;
        };
        previewImage.src = previewUrl;
      }
      camera.fps = Number(payload.fps || camera.fps || 0).toFixed(2);
      camera.frames = payload.completed || payload.frame_id || camera.frames;
    }

    function upsertAlarm(event) {
      if (!event?.id) return;
      const index = alarms.value.findIndex((item) => item.id === event.id);
      if (index >= 0) alarms.value[index] = { ...alarms.value[index], ...event };
      else alarms.value.unshift(event);
      alarms.value.sort((left, right) => Number(right.happened_at || 0) - Number(left.happened_at || 0));
      alarms.value = alarms.value.slice(0, 200);
    }

    function playAlarmSound() {
      if (!alarmSoundEnabled.value) return;
      try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        const context = new AudioContext();
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        oscillator.frequency.value = 880;
        gain.gain.setValueAtTime(0.12, context.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.7);
        oscillator.connect(gain).connect(context.destination);
        oscillator.start();
        oscillator.stop(context.currentTime + 0.7);
      } catch (_) {
        // Visual and system notifications still work when WebAudio is unavailable.
      }
    }

    function notifyAlarm(event) {
      if (!alarmSoundEnabled.value) return;
      playAlarmSound();
      if (window.Notification && Notification.permission === "granted") {
        new Notification("检测到人员跌倒", {
          body: `${event.camera_id} / 置信度 ${Math.round(Number(event.confidence || 0) * 100)}%`,
        });
      }
    }

    async function toggleAlarmNotifications() {
      if (alarmSoundEnabled.value) {
        alarmSoundEnabled.value = false;
        localStorage.setItem("alarmSoundEnabled", "false");
        message.value = "声音与系统通知已关闭";
        return;
      }
      alarmSoundEnabled.value = true;
      localStorage.setItem("alarmSoundEnabled", "true");
      if (window.Notification && Notification.permission === "default") await Notification.requestPermission();
      message.value = "前端告警声音与系统通知已启用";
    }

    function connectWs() {
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${protocol}://${location.host}/ws/detections`);
      ws.onopen = () => {
        wsConnected.value = true;
        appendLog("WebSocket 已连接");
      };
      ws.onclose = () => {
        wsConnected.value = false;
        appendLog("WebSocket 已断开，2 秒后重连");
        setTimeout(connectWs, 2000);
      };
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === "status") {
          applyStatus(data.payload);
        } else if (data.type === "detection") {
          updateCameraDetection(data.camera_id || data.payload.camera_id || "cam1", data.payload);
        } else if (data.type === "log") {
          appendLog(data.payload.message, data.camera_id);
        } else if (data.type === "alarm_history") {
          alarms.value = Array.isArray(data.payload?.events) ? data.payload.events : [];
        } else if (data.type === "alarm") {
          upsertAlarm(data.payload);
          notifyAlarm(data.payload);
          appendLog("检测到人员跌倒，已自动开始事件录像", data.camera_id);
        } else if (data.type === "alarm_update") {
          upsertAlarm(data.payload);
        } else if (data.type === "alarm_deleted") {
          alarms.value = alarms.value.filter((item) => item.id !== data.payload?.id);
        }
      };
    }

    async function callApi(path, body = {}, method = "POST") {
      message.value = "";
      const response = await fetch(path, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "请求失败");
      message.value = payload.message || "操作完成";
      appendLog(message.value);
      await refreshStatus();
    }

    async function refreshStatus() {
      const response = await fetch("/api/status");
      applyStatus(await response.json());
    }

    async function refreshSystemMetrics() {
      try {
        const response = await fetch("/api/system/metrics");
        const payload = await response.json();
        Object.assign(system, payload);
        pushMetricHistory(payload);
      } catch (err) {
        appendLog(`刷新系统监控失败：${err.message}`);
      }
    }

    async function refreshEvents() {
      try {
        const response = await fetch("/api/events?limit=100");
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "读取告警失败");
        alarms.value = Array.isArray(payload.events) ? payload.events : [];
      } catch (err) {
        appendLog(`刷新告警事件失败：${err.message}`);
      }
    }

    async function refreshAlarmSettings() {
      try {
        const response = await fetch("/api/alarm-settings");
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "读取告警设置失败");
        alarmSettings.confidenceThresholdPercent = Math.round(Number(payload.confidence_threshold || 0) * 100);
        alarmSettings.recordingEnabled = Boolean(payload.recording_enabled);
        alarmSettings.recordingDirectory = payload.recording_directory || "";
      } catch (err) {
        appendLog(`读取告警设置失败：${err.message}`);
      }
    }

    async function saveAlarmSettings() {
      const percent = Math.max(0, Math.min(100, Number(alarmSettings.confidenceThresholdPercent) || 0));
      alarmSettings.confidenceThresholdPercent = percent;
      try {
        await callApi("/api/alarm-settings", { confidence_threshold: percent / 100 }, "PUT");
        await refreshAlarmSettings();
      } catch (err) {
        message.value = err.message;
        appendLog(`保存告警设置失败：${err.message}`);
      }
    }

    async function acknowledgeAlarm(event) {
      try {
        const response = await fetch(`/api/events/${event.id}/acknowledge`, { method: "POST" });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "确认失败");
        upsertAlarm(payload.event);
      } catch (err) {
        appendLog(`确认告警失败：${err.message}`, event.camera_id);
      }
    }

    async function deleteAlarm(event) {
      if (!confirm("确定删除这条告警记录及其录像文件？删除后无法恢复。")) return;
      try {
        const response = await fetch(`/api/events/${event.id}`, { method: "DELETE" });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "删除失败");
        alarms.value = alarms.value.filter((item) => item.id !== event.id);
        message.value = payload.message || "告警已删除";
        appendLog(message.value, event.camera_id);
      } catch (err) {
        message.value = err.message;
        appendLog(`删除告警失败：${err.message}`, event.camera_id);
      }
    }

    function eventTime(value) {
      if (!value) return "--";
      return new Date(Number(value) * 1000).toLocaleString();
    }

    function eventVideoUrl(event) {
      return event.video_ready ? `/api/events/${event.id}/video` : "";
    }

    function recordingTime(value) {
      if (!value) return "录像";
      return new Date(Number(value) * 1000).toLocaleString();
    }

    function formatBytes(value) {
      const bytes = Number(value || 0);
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
      return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    }

    function pushMetricHistory(payload) {
      const label = new Date().toLocaleTimeString();
      const npuTemp = Array.isArray(payload.temperatures)
        ? payload.temperatures.find((item) => String(item.name || "").includes("npu"))
        : null;
      metricHistory.labels.push(label);
      metricHistory.cpu.push(Number(payload.cpu?.usage_percent ?? 0));
      metricHistory.memory.push(Number(payload.memory?.used_percent ?? 0));
      metricHistory.npu.push(Number(payload.npu?.load_percent ?? 0));
      metricHistory.rga.push(Number(payload.rga?.debug?.load_percent ?? 0));
      metricHistory.temp.push(Number((npuTemp && npuTemp.temp_c) || payload.npu?.temp_c || 0));
      for (const key of ["labels", "cpu", "memory", "npu", "rga", "temp"]) {
        if (metricHistory[key].length > 40) metricHistory[key].shift();
      }
    }

    async function startPipeline(camera) {
      const item = camera.form || formForCamera(camera.id);
      try {
        await callApi(`/api/cameras/${camera.id}/pipeline/start`, {
          source: item.source || null,
          contexts: item.contexts || null,
          dry_run: Boolean(item.dryRun),
        });
      } catch (err) {
        message.value = err.message;
        appendLog(`启动检测失败：${err.message}`, camera.id);
      }
    }

    async function stopPipeline(camera) {
      await callApi(`/api/cameras/${camera.id}/pipeline/stop`);
    }

    async function startRecording(camera) {
      await callApi(`/api/cameras/${camera.id}/recording/start`);
    }

    async function stopRecording(camera) {
      await callApi(`/api/cameras/${camera.id}/recording/stop`);
    }

    async function startStream(camera) {
      await callApi(`/api/cameras/${camera.id}/stream/start`);
    }

    async function stopStream(camera) {
      await callApi(`/api/cameras/${camera.id}/stream/stop`);
    }

    async function addCamera() {
      try {
        await callApi("/api/cameras", {
          id: newCamera.id.trim(),
          name: newCamera.name.trim(),
          source_url: newCamera.source_url.trim(),
          width: Number(newCamera.width) || 640,
          height: Number(newCamera.height) || 360,
          contexts: Number(newCamera.contexts) || 8,
          decoder: newCamera.decoder || "software",
        });
        newCamera.id = "";
        newCamera.name = "";
        newCamera.source_url = "";
        newCamera.width = 640;
        newCamera.height = 360;
        newCamera.contexts = 8;
        newCamera.decoder = "software";
      } catch (err) {
        message.value = err.message;
        appendLog(`添加摄像头失败：${err.message}`);
      }
    }

    async function removeCamera(camera) {
      if (!confirm(`确定移除摄像头 ${camera.id}？请先停止该路推流/检测/录像。`)) return;
      try {
        const response = await fetch(`/api/cameras/${camera.id}`, { method: "DELETE" });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "请求失败");
        message.value = payload.message || "已移除";
        appendLog(message.value);
        await refreshStatus();
      } catch (err) {
        message.value = err.message;
        appendLog(`移除摄像头失败：${err.message}`, camera.id);
      }
    }

    function latestDetections(camera) {
      return camera.last_result?.detections || [];
    }

    function fmt(value, suffix = "") {
      if (value === null || value === undefined || value === "") return "N/A";
      return `${value}${suffix}`;
    }

    function busyText(value) {
      if (value === null || value === undefined) return "采样中";
      return `${value}%`;
    }

    function barStyle(value) {
      const percent = value === null || value === undefined ? 0 : Math.max(0, Math.min(100, Number(value)));
      return { width: `${percent}%` };
    }

    function percentValue(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return 0;
      return Math.max(0, Math.min(100, Number(value)));
    }

    function gaugeDash(value) {
      return `${percentValue(value)} 100`;
    }

    function avgBusy(items = []) {
      const values = items
        .map((item) => item.busy_percent)
        .filter((value) => value !== null && value !== undefined)
        .map(Number);
      if (!values.length) return null;
      return Number((values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(1));
    }

    function maxLoad(items = []) {
      const values = items
        .map((item) => item.load_percent)
        .filter((value) => value !== null && value !== undefined)
        .map(Number);
      if (!values.length) return null;
      return Number(Math.max(...values).toFixed(1));
    }

    function seriesStats(seriesList = []) {
      const values = seriesList.flat().map(Number).filter((value) => Number.isFinite(value));
      if (!values.length) return { min: 0, max: 100 };
      let min = Math.min(...values);
      let max = Math.max(...values);
      if (max - min < 8) {
        const mid = (max + min) / 2;
        min = mid - 4;
        max = mid + 4;
      }
      min = Math.max(0, Math.floor(min - 1));
      max = Math.min(100, Math.ceil(max + 1));
      if (max <= min) max = min + 1;
      return { min, max };
    }

    function resourceStats() {
      return seriesStats([metricHistory.cpu, metricHistory.memory, metricHistory.npu, metricHistory.rga]);
    }

    function polylinePoints(values = [], width = 320, height = 100, stats = null) {
      if (!values.length) return "";
      const range = stats || seriesStats([values]);
      const span = Math.max(1, range.max - range.min);
      if (values.length === 1) return `0,${height - ((values[0] - range.min) / span) * height}`;
      return values
        .map((value, index) => {
          const x = (index / (values.length - 1)) * width;
          const normalized = (Number(value) - range.min) / span;
          const y = height - Math.max(0, Math.min(1, normalized)) * height;
          return `${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(" ");
    }

    function chartPoints(values = [], labels = [], width = 320, height = 100, stats = null, name = "", unit = "%") {
      if (!values.length) return [];
      const range = stats || seriesStats([values]);
      const span = Math.max(1, range.max - range.min);
      return values.map((value, index) => {
        const x = values.length === 1 ? 0 : (index / (values.length - 1)) * width;
        const normalized = (Number(value) - range.min) / span;
        const y = height - Math.max(0, Math.min(1, normalized)) * height;
        return {
          x: Number(x.toFixed(1)),
          y: Number(y.toFixed(1)),
          label: `${labels[index] || ""} ${name}: ${Number(value).toFixed(1)}${unit}`,
        };
      });
    }

    function tempPolylinePoints(values = [], width = 320, height = 100) {
      const valid = values.filter((value) => Number(value) > 0);
      if (!valid.length) return "";
      const min = Math.min(...valid, 20);
      const max = Math.max(...valid, 90);
      const range = Math.max(1, max - min);
      if (values.length === 1) return `0,${height - ((values[0] - min) / range) * height}`;
      return values
        .map((value, index) => {
          const x = (index / (values.length - 1)) * width;
          const y = height - ((Number(value) - min) / range) * height;
          return `${x.toFixed(1)},${Math.max(0, Math.min(height, y)).toFixed(1)}`;
        })
        .join(" ");
    }

    function tempChartPoints(values = [], labels = [], width = 320, height = 100) {
      const valid = values.filter((value) => Number(value) > 0);
      if (!valid.length) return [];
      const min = Math.min(...valid, 20);
      const max = Math.max(...valid, 90);
      const range = Math.max(1, max - min);
      return values.map((value, index) => {
        const x = values.length === 1 ? 0 : (index / (values.length - 1)) * width;
        const y = height - ((Number(value) - min) / range) * height;
        return {
          x: Number(x.toFixed(1)),
          y: Number(Math.max(0, Math.min(height, y)).toFixed(1)),
          label: `${labels[index] || ""} 温度: ${Number(value).toFixed(1)}℃`,
        };
      });
    }

    function showChartTooltip(point, event) {
      chartTooltip.visible = true;
      chartTooltip.text = point.label;
      chartTooltip.x = event.clientX + 14;
      chartTooltip.y = event.clientY + 14;
    }

    function hideChartTooltip() {
      chartTooltip.visible = false;
    }

    function setMonitorSize(size) {
      if (!monitorSizeOptions.some((option) => option.value === size)) return;
      monitorSize.value = size;
      localStorage.setItem("monitorSize", size);
    }

    function monitorGridClass() {
      return `monitor-size-${monitorSize.value}`;
    }

    function tooltipStyle() {
      return {
        left: `${chartTooltip.x}px`,
        top: `${chartTooltip.y}px`,
      };
    }

    function boxStyle(camera, box = {}) {
      const width = Number(camera.last_result?.width || camera.width) || 640;
      const height = Number(camera.last_result?.height || camera.height) || 360;
      const x = Math.max(0, Number(box.x || 0));
      const y = Math.max(0, Number(box.y || 0));
      const w = Math.max(0, Number(box.w || 0));
      const h = Math.max(0, Number(box.h || 0));
      return {
        left: `${Math.min(100, (x / width) * 100)}%`,
        top: `${Math.min(100, (y / height) * 100)}%`,
        width: `${Math.min(100, (w / width) * 100)}%`,
        height: `${Math.min(100, (h / height) * 100)}%`,
      };
    }

    async function saveCamera(camera) {
      const edit = camera.edit || editFormForCamera(camera);
      try {
        await callApi(`/api/cameras/${camera.id}`, {
          id: camera.id,
          name: edit.name.trim(),
          source_url: edit.source_url.trim(),
          width: Number(edit.width) || 640,
          height: Number(edit.height) || 360,
          contexts: Number(edit.contexts) || 1,
          decoder: edit.decoder || "software",
        }, "PUT");
      } catch (err) {
        message.value = err.message;
        appendLog(`修改摄像头失败：${err.message}`, camera.id);
      }
    }

    function poseViewBox(camera) {
      const width = Number(camera.last_result?.width || camera.width) || 640;
      const height = Number(camera.last_result?.height || camera.height) || 360;
      return `0 0 ${width} ${height}`;
    }

    const cocoSkeleton = [
      [0, 1], [0, 2], [1, 3], [2, 4],
      [5, 6], [5, 7], [7, 9], [6, 8], [8, 10],
      [5, 11], [6, 12], [11, 12],
      [11, 13], [13, 15], [12, 14], [14, 16],
    ];

    function visibleKeypoints(detection) {
      return (detection?.keypoints || [])
        .map((point, index) => ({ ...point, index }))
        .filter((point) => Number(point.score || 0) >= 0.25);
    }

    function skeletonLines(detection) {
      const points = detection?.keypoints || [];
      return cocoSkeleton.flatMap(([first, second]) => {
        const left = points[first];
        const right = points[second];
        if (!left || !right || Number(left.score || 0) < 0.25 || Number(right.score || 0) < 0.25) return [];
        return [{ x1: left.x, y1: left.y, x2: right.x, y2: right.y }];
      });
    }

    const postureLabels = {
      standing: "站立",
      walking: "行走",
      sitting: "坐姿",
      lying_down: "躺卧",
      stand_up: "起身",
      sit_down: "落座",
      fall_down: "跌倒",
      // Compatibility with results produced by an older pipeline during a rolling update.
      normal: "站立",
      suspected_fall: "疑似跌倒",
      fall: "跌倒",
    };

    function postureLabel(detection) {
      return detection?.action_label || postureLabels[detection?.action] || "人员";
    }

    function isFallDetection(detection) {
      return ["candidate", "fallen"].includes(detection?.fall_state)
        || ["fall_down", "suspected_fall", "fall"].includes(detection?.action);
    }

    function detectionConfidence(detection) {
      return Number(isFallDetection(detection) ? detection?.fall_score : detection?.score) || 0;
    }

    onMounted(() => {
      refreshStatus();
      refreshSystemMetrics();
      refreshEvents();
      refreshAlarmSettings();
      connectWs();
      setInterval(refreshStatus, 3000);
      setInterval(refreshSystemMetrics, 2000);
      setInterval(refreshEvents, 10000);
    });

    return {
      status,
      system,
      form,
      newCamera,
      activeTab,
      monitorSize,
      monitorSizeOptions,
      chartTooltip,
      metricHistory,
      wsConnected,
      message,
      logs,
      alarms,
      alarmSoundEnabled,
      alarmSettings,
      activeAlarm,
      unacknowledgedAlarms,
      totalRunning,
      totalStreaming,
      latestDetections,
      fmt,
      busyText,
      barStyle,
      percentValue,
      gaugeDash,
      avgBusy,
      maxLoad,
      resourceStats,
      polylinePoints,
      chartPoints,
      tempPolylinePoints,
      tempChartPoints,
      showChartTooltip,
      hideChartTooltip,
      setMonitorSize,
      monitorGridClass,
      tooltipStyle,
      startPipeline,
      stopPipeline,
      startRecording,
      stopRecording,
      startStream,
      stopStream,
      addCamera,
      saveCamera,
      removeCamera,
      boxStyle,
      poseViewBox,
      visibleKeypoints,
      skeletonLines,
      postureLabel,
      isFallDetection,
      detectionConfidence,
      toggleAlarmNotifications,
      saveAlarmSettings,
      acknowledgeAlarm,
      deleteAlarm,
      eventTime,
      eventVideoUrl,
      recordingTime,
      formatBytes,
    };
  },
}).mount("#app");
