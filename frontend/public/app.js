"use strict";
const $ = (id) => document.getElementById(id);

async function loadHealth() {
 const badge = $("provider-badge");
 try {
  const res = await fetch("/health");
  const data = (await res.json()).data;
  if (!data.key_configured) {
   badge.textContent = "no API key";
   badge.classList.add("stub");
  } else {
   badge.textContent = `${data.provider} · ${data.model}`;
  }
 } catch {
  badge.textContent = "backend unreachable";
  badge.classList.add("stub");
 }
}

async function runAgent() {
 const btn = $("run-btn");
 const status = $("status");
 const errBox = $("error");
 const wrap = $("result-wrap");

 const question = ($("question")?.value || "").trim();
 const file = $("csv")?.files?.[0];

 errBox.hidden = true;
 wrap.hidden = true;

 if (!question) {
  errBox.textContent = "Enter a question — it can't be empty.";
  errBox.hidden = false;
  return;
 }

 btn.disabled = true;
 status.hidden = false;

 try {
  const form = new FormData();
  form.append("question", question);
  if (file) form.append("csv_upload", file, file.name);

  const res = await fetch("/runs", { method: "POST", body: form });
  const payload = await res.json();
  if (!res.ok) throw new Error(payload?.detail?.message || `HTTP ${res.status}`);

  const run = payload.data;
  $("result").textContent = run.answer_text || run.output_text || "";
  $("result-meta").textContent = `${run.run_id} · ${run.provider} · ${run.model}`;
  wrap.hidden = false;
 } catch (err) {
  errBox.textContent = err.message;
  errBox.hidden = false;
 } finally {
  btn.disabled = false;
  status.hidden = true;
 }
}

$("run-btn").addEventListener("click", runAgent);
loadHealth();
