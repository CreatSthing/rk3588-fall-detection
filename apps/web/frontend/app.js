const { createApp, computed, onMounted, reactive, ref } = Vue;

createApp({
  setup() {
    const status = reactive({
      running: false,
      recording: false,
      streaming: false,
      fps: 0,
      frames: 0,
      uptime_sec: 0,
      last_error: null,
      last_result: null,
    });
    const form = reactive({
      source: "",
      contexts: 8,
      dryRun: false,
      videoWidth: 1920,
      videoHeight: 1080,
      playerUrl: `${location.protocol}//${location.hostname}:8889/live/raw`,
    });
    const wsConnected = ref(false);
    const message = ref("");
    const logs = ref([]);

    const latestDetections = computed(() => status.last_result?.detections || []);
    const rtspUrl = computed(() => `rtsp://${location.hostname}:8554/live/raw`);
    const hlsUrl = computed(() => `${location.protocol}//${location.hostname}:8888/live/raw/index.m3u8`);

    function appendLog(text) {
      const now = new Date().toLocaleTimeString();
      logs.value.unshift(`[${now}] ${text}`);
      logs.value = logs.value.slice(0, 100);
    }

    function applyStatus(payload) {
      Object.assign(status, payload);
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
          status.last_result = data.payload;
          status.fps = Number(data.payload.fps || status.fps).toFixed(2);
          status.frames = data.payload.completed || data.payload.frame_id || status.frames;
        } else if (data.type === "log") {
          appendLog(data.payload.message);
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

    async function startPipeline() {
      try {
        await callApi("/api/pipeline/start", {
          source: form.source || null,
          contexts: form.contexts || null,
          dry_run: form.dryRun,
        });
      } catch (err) {
        message.value = err.message;
        appendLog(`启动检测失败：${err.message}`);
      }
    }

    async function stopPipeline() {
      await callApi("/api/pipeline/stop");
    }

    async function startRecording() {
      await callApi("/api/recording/start");
    }

    async function stopRecording() {
      await callApi("/api/recording/stop");
    }

    async function startStream() {
      await callApi("/api/stream/start");
    }

    async function stopStream() {
      await callApi("/api/stream/stop");
    }

    function boxStyle(box = {}) {
      const width = Number(form.videoWidth) || 1920;
      const height = Number(form.videoHeight) || 1080;
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
      latestDetections,
      rtspUrl,
      hlsUrl,
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
