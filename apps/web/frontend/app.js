const { createApp, computed, onMounted, reactive, ref } = Vue;

createApp({
  setup() {
    const status = reactive({
      cameras: [],
    });
    const form = reactive({
      contexts: 8,
      dryRun: false,
    });
    const wsConnected = ref(false);
    const message = ref("");
    const logs = ref([]);
    const cameraFormCache = {};

    const totalRunning = computed(() => status.cameras.filter((camera) => camera.running).length);
    const totalStreaming = computed(() => status.cameras.filter((camera) => camera.streaming).length);

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

    function appendLog(text, cameraId = "") {
      const now = new Date().toLocaleTimeString();
      const prefix = cameraId ? `[${cameraId}] ` : "";
      logs.value.unshift(`[${now}] ${prefix}${text}`);
      logs.value = logs.value.slice(0, 160);
    }

    function applyStatus(payload) {
      const cameras = Array.isArray(payload.cameras) ? payload.cameras : [payload].filter(Boolean);
      status.cameras = cameras.map((camera) => ({
        ...camera,
        form: formForCamera(camera.id),
        player_url_abs: normalizePlayerUrl(camera.player_url),
        rtsp_url_abs: camera.rtsp_url || `rtsp://${location.hostname}:8554/live/${camera.id}`,
        hls_url_abs: camera.hls_url && camera.hls_url.startsWith("http")
          ? camera.hls_url
          : `${location.protocol}//${location.hostname}:8888${camera.hls_url || `/live/${camera.id}/index.m3u8`}`,
      }));
    }

    function updateCameraDetection(cameraId, payload) {
      const camera = status.cameras.find((item) => item.id === cameraId);
      if (!camera) return;
      camera.last_result = payload;
      camera.fps = Number(payload.fps || camera.fps || 0).toFixed(2);
      camera.frames = payload.completed || payload.frame_id || camera.frames;
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
        }
      };
    }

    async function callApi(path, body = {}) {
      message.value = "";
      const response = await fetch(path, {
        method: "POST",
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

    function latestDetections(camera) {
      return camera.last_result?.detections || [];
    }

    function boxStyle(camera, box = {}) {
      const width = Number(camera.width) || 640;
      const height = Number(camera.height) || 360;
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

    onMounted(() => {
      refreshStatus();
      connectWs();
      setInterval(refreshStatus, 3000);
    });

    return {
      status,
      form,
      wsConnected,
      message,
      logs,
      totalRunning,
      totalStreaming,
      latestDetections,
      startPipeline,
      stopPipeline,
      startRecording,
      stopRecording,
      startStream,
      stopStream,
      boxStyle,
    };
  },
}).mount("#app");
