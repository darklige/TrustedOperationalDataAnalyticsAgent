import assert from "node:assert/strict";
import { test } from "node:test";
import { createRunUI } from "../src/trust_agent/web/app.mjs";

class Element {
  textContent = "";
  disabled = false;
  value = "";
  listeners = new Map();
  addEventListener(kind, callback) { this.listeners.set(kind, callback); }
  fire(kind) { this.listeners.get(kind)?.({ preventDefault() {} }); }
}

class Source {
  static instances = [];
  listeners = new Map();
  closed = false;
  constructor(url) { this.url = url; Source.instances.push(this); }
  addEventListener(kind, callback) { this.listeners.set(kind, callback); }
  emit(kind, seq, data = {}, runId = "run-one") {
    this.listeners.get(kind)?.({ data: JSON.stringify({
      run_id: runId, kind, seq, data,
    }), lastEventId: String(seq) });
  }
  close() { this.closed = true; }
}

function setup() {
  Source.instances = [];
  const elements = Object.fromEntries([
    "question-form", "question", "answer", "status", "submit", "cancel", "reconnect",
  ].map((id) => [id, new Element()]));
  const document = { getElementById: (id) => elements[id] };
  const ui = createRunUI({ document, EventSourceClass: Source,
    fetchImpl: async () => ({ ok: true, json: async () => ({ run_id: "run-one" }) }),
    location: { search: "" }, history: { replaceState() {} } }).mount();
  return { ui, elements };
}

test("draft is never displayed; discarded draft stays hidden after replay and commit", () => {
  const { ui, elements: e } = setup();
  const source = ui.connect("run-one");
  source.emit("run_started", 1, { question: "count" });
  source.emit("text_delta", 2, { attempt_id: "bad", provisional: true,
    text: "未经验证的 999 条" });
  assert.equal(e.answer.textContent, "");
  assert.deepEqual(ui.getState().pendingAttempts, ["bad"]);
  source.emit("text_discarded", 3, { attempt_id: "bad", reason: "answer_rejected" });
  source.emit("text_delta", 2, { attempt_id: "bad", provisional: true,
    text: "duplicate" });
  assert.deepEqual(ui.getState().pendingAttempts, []);
  const resumed = ui.connect("run-one", ui.getState().lastSeq);
  assert.equal(resumed.url, "/runs/run-one/events?after=3");
  assert.equal(source.closed, true);
  resumed.emit("text_delta", 4, { attempt_id: "good", provisional: true,
    text: "准确答案" });
  assert.equal(e.answer.textContent, "");
  resumed.emit("text_committed", 5, { attempt_id: "good", text: "准确答案" });
  assert.equal(e.answer.textContent, "准确答案");
  resumed.emit("run_completed", 6, { attempt_id: "good", answer: "准确答案" });
  assert.equal(e.status.textContent, "完成");
  assert.equal(resumed.closed, true);
  assert.equal(ui.getState().terminal, true);
});

test("failed and cancelled runs remove all pending text and close connection", () => {
  for (const [kind, label] of [["run_failed", "分析失败"],
    ["run_cancelled", "已取消"]]) {
    const { ui, elements: e } = setup();
    const source = ui.connect("run-one");
    source.emit("text_delta", 10, { attempt_id: "draft", provisional: true,
      text: "unsafe" });
    source.emit(kind, 11);
    assert.equal(e.answer.textContent, "");
    assert.equal(e.status.textContent, label);
    assert.deepEqual(ui.getState().pendingAttempts, []);
    assert.equal(source.closed, true);
  }
});

test("a mismatched completion clears the displayed commit", () => {
  const { ui, elements: e } = setup();
  const source = ui.connect("run-one");
  source.emit("text_committed", 1, { attempt_id: "a", text: "A" });
  assert.equal(e.answer.textContent, "A");
  source.emit("run_completed", 2, { attempt_id: "b", answer: "B" });
  assert.equal(e.answer.textContent, "");
  assert.equal(e.status.textContent, "回答事件校验失败");
});

test("older sources, other runs, and duplicate sequence IDs cannot alter current view", () => {
  const { ui, elements: e } = setup();
  const old = ui.connect("run-one");
  const fresh = ui.connect("run-one");
  old.emit("text_committed", 2, { attempt_id: "old", text: "old" });
  fresh.emit("text_committed", 2, { attempt_id: "other", text: "other" }, "run-two");
  assert.equal(e.answer.textContent, "");
  fresh.emit("text_committed", 4, { attempt_id: "new", text: "new" });
  fresh.emit("text_committed", 4, { attempt_id: "duplicate", text: "duplicate" });
  assert.equal(e.answer.textContent, "new");
});

test("reloading a run replays from zero and restores a committed answer", () => {
  Source.instances = [];
  const elements = Object.fromEntries([
    "question-form", "question", "answer", "status", "submit", "cancel", "reconnect",
  ].map((id) => [id, new Element()]));
  const ui = createRunUI({ document: { getElementById: (id) => elements[id] },
    EventSourceClass: Source, fetchImpl: async () => {},
    location: { search: "?run=run-one" } }).mount();
  const source = Source.instances.at(-1);
  assert.equal(source.url, "/runs/run-one/events?after=0");
  source.emit("run_started", 1);
  source.emit("text_delta", 2, { attempt_id: "a", provisional: true, text: "draft" });
  source.emit("text_discarded", 3, { attempt_id: "a" });
  source.emit("text_committed", 4, { attempt_id: "b", text: "checked" });
  source.emit("run_completed", 5, { attempt_id: "b", answer: "checked" });
  assert.equal(elements.answer.textContent, "checked");
  assert.equal(ui.getState().lastSeq, 5);
});

test("starting a new run disables old-run controls until the new ID arrives", async () => {
  const { ui, elements: e } = setup();
  ui.connect("old-run");
  const pending = ui.start("new question");
  assert.equal(ui.getState().runId, null);
  assert.equal(e.cancel.disabled, true);
  assert.equal(e.reconnect.disabled, true);
  await pending;
  assert.equal(ui.getState().runId, "run-one");
  assert.equal(e.cancel.disabled, false);
});
