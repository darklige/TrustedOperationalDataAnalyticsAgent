// The browser may receive a draft before the evidence gate has decided whether
// to keep it. Only text_committed is allowed to change the visible answer.
export function createRunUI({ document, EventSourceClass, fetchImpl, location, history }) {
  const form = document.getElementById("question-form");
  const question = document.getElementById("question");
  const answer = document.getElementById("answer");
  const status = document.getElementById("status");
  const submit = document.getElementById("submit");
  const cancel = document.getElementById("cancel");
  const reconnect = document.getElementById("reconnect");
  let source = null;
  let runId = null;
  let lastSeq = 0;
  let draftByAttempt = new Map();
  let committed = null;
  let terminal = false;

  const setStatus = (value) => { status.textContent = value; };
  const clearDrafts = () => { draftByAttempt.clear(); };
  const stopStream = () => {
    if (source) source.close();
    source = null;
  };
  const finish = () => {
    terminal = true;
    clearDrafts();
    stopStream();
    cancel.disabled = true;
    reconnect.disabled = true;
    submit.disabled = false;
  };
  const reset = () => {
    stopStream();
    lastSeq = 0;
    clearDrafts();
    committed = null;
    terminal = false;
    answer.textContent = "";
    cancel.disabled = false;
    reconnect.disabled = false;
    submit.disabled = true;
    setStatus("连接中");
  };

  function applyEvent(event) {
    if (!event || !Number.isSafeInteger(event.seq) || event.seq <= lastSeq) return;
    // A missing sequence can be normal: SQLite sequence numbers are global to all runs.
    lastSeq = event.seq;
    const data = event.data || {};
    switch (event.kind) {
      case "run_started":
        clearDrafts();
        committed = null;
        terminal = false;
        answer.textContent = "";
        setStatus("分析中");
        break;
      case "run_resumed":
      case "loop_started":
        setStatus("分析中");
        break;
      case "text_delta":
        if (data.provisional === true && typeof data.attempt_id === "string" &&
            typeof data.text === "string") {
          draftByAttempt.set(data.attempt_id,
            (draftByAttempt.get(data.attempt_id) || "") + data.text);
        }
        break;
      case "text_discarded":
        draftByAttempt.delete(data.attempt_id);
        if (committed?.attemptId === data.attempt_id) {
          committed = null;
          answer.textContent = "";
        }
        break;
      case "text_committed":
        if (typeof data.attempt_id !== "string" || typeof data.text !== "string") break;
        draftByAttempt.delete(data.attempt_id);
        committed = { attemptId: data.attempt_id, text: data.text };
        answer.textContent = data.text;
        break;
      case "run_completed":
        if (!committed || committed.attemptId !== data.attempt_id ||
            committed.text !== data.answer) {
          answer.textContent = "";
          setStatus("回答事件校验失败");
        } else {
          setStatus("完成");
        }
        finish();
        break;
      case "run_failed":
        committed = null;
        answer.textContent = "";
        setStatus("分析失败");
        finish();
        break;
      case "run_cancelled":
        committed = null;
        answer.textContent = "";
        setStatus("已取消");
        finish();
        break;
      default:
        break;
    }
  }

  function connect(id, after = 0) {
    if (after === 0) reset();
    else stopStream();
    runId = id;
    const url = `/runs/${encodeURIComponent(id)}/events?after=${after}`;
    source = new EventSourceClass(url);
    const activeSource = source;
    const kinds = ["run_started", "run_resumed", "loop_started", "text_delta",
      "text_discarded", "text_committed", "run_completed", "run_failed", "run_cancelled"];
    for (const kind of kinds) {
      activeSource.addEventListener(kind, (message) => {
        if (source !== activeSource) return;
        try {
          const event = JSON.parse(message.data);
          if (event.run_id === runId && event.kind === kind) applyEvent(event);
        } catch {
          setStatus("事件解析失败，正在等待后续事件");
        }
      });
    }
    activeSource.onerror = () => {
      if (!terminal && source === activeSource) setStatus("连接中断，正在重连");
    };
    return activeSource;
  }

  async function start(value) {
    stopStream();
    reset();
    runId = null;
    cancel.disabled = true;
    reconnect.disabled = true;
    try {
      const response = await fetchImpl("/runs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: value }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (typeof payload.run_id !== "string") throw new Error("missing run_id");
      history?.replaceState(null, "", `?run=${encodeURIComponent(payload.run_id)}`);
      connect(payload.run_id);
    } catch {
      setStatus("无法开始分析");
      cancel.disabled = true;
      reconnect.disabled = true;
      submit.disabled = false;
    }
  }

  function mount() {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (question.value.trim()) void start(question.value.trim());
    });
    cancel.addEventListener("click", async () => {
      if (runId && !terminal) {
        cancel.disabled = true;
        try {
          const response = await fetchImpl(`/runs/${encodeURIComponent(runId)}/cancel`,
            { method: "POST" });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          setStatus("正在取消");
        } catch {
          cancel.disabled = false;
          setStatus("取消请求失败");
        }
      }
    });
    reconnect.addEventListener("click", () => {
      if (runId && !terminal) connect(runId, lastSeq);
    });
    const existing = new URLSearchParams(location?.search || "").get("run");
    if (existing) connect(existing);
    return this;
  }

  return { mount, start, connect, applyEvent,
    getState: () => ({ runId, lastSeq, pendingAttempts: [...draftByAttempt.keys()],
      committed, terminal }) };
}

if (typeof document !== "undefined") {
  createRunUI({ document, EventSourceClass: EventSource, fetchImpl: fetch,
    location: window.location, history: window.history }).mount();
}
