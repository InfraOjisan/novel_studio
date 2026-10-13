/* novel_studio — 画面。サーバーの /api を呼ぶだけで、状態はすべてサーバー（works/<slug>/）にある。 */
"use strict";
const STAGES = ["theme", "propose", "classify", "brief", "fix", "canon", "write", "finish"];
const STAGE_JA = { theme: "テーマ", propose: "提案", classify: "分類", brief: "BRIEF", fix: "確定", canon: "CANON", write: "執筆", finish: "仕上げ" };
const FIELDS_JA = { kind: "種類", genre: "ジャンル", title: "仮タイトル", setting: "舞台設定", synopsis: "あらすじ", highlight: "見せ場",
  appeal: "何が面白いか", must_keep: "譲れないポイント", genre_reason: "このジャンルである必然" };
const ROLE_JA = { writer: "書き手（設計・本文）", reviewer: "審査役（審査・記録）", polisher: "磨き（任意）", premium: "上位モデル（例外処理・校正だけ）" };
const S = { slug: "", snap: null, models: [], settings: {}, view: null, briefV: null, briefTab: "read", jobs: {}, chapterView: null };

const $ = (q, el = document) => el.querySelector(q);
const h = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const usd = (x) => "$" + (Number(x) || 0).toFixed(4);
const short = (m) => String(m || "").split("/").pop();

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), err ? 7000 : 3000);
}

async function file(rel) {
  const d = await api("GET", `/api/works/${S.slug}/file?path=${encodeURIComponent(rel)}`).catch(() => null);
  return d && d.text != null ? d : null;
}

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}

async function waitJob(jid, label) {
  S.jobs[jid] = label;
  render();
  try {
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      const j = await api("GET", `/api/jobs/${jid}`);
      if (j.state === "done") return j.result;
      if (j.state === "error") throw new Error(j.error);
    }
  } finally {
    delete S.jobs[jid];
    await refresh();
  }
}

async function guard(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); }
}

/* ---------------------------------------------------------------- 読み込み */
async function loadWorks(select) {
  const d = await api("GET", "/api/works");
  const sel = $("#workSel");
  sel.innerHTML = `<option value="">— 選ぶ —</option>` + d.works.map((w) => `<option value="${h(w.slug)}">${h(w.slug)}　${h(w.title)}</option>`).join("");
  if (select) sel.value = select;
  const ks = $("#keyState");
  ks.textContent = d.has_key ? (d.base_url.includes("openrouter.ai") ? "キー：あり" : "モデル：" + d.base_url) : "APIキーがありません（.env）";
  ks.className = "keystate" + (d.has_key ? "" : " bad");
}

async function loadModels() {
  const d = await api("GET", "/api/models");
  S.models = d.models;
  S.settings = d.settings;
}

async function refresh() {
  if (!S.slug) { S.snap = null; render(); return; }
  try {
    S.snap = await api("GET", `/api/works/${S.slug}`);
  } catch (e) { toast(e.message, true); return; }
  if (!S.view) S.view = S.snap.stage;
  render();
}

function busy() {
  const eng = S.snap && S.snap.engine;
  // エンジンが進んでいる途中（承認待ち・完成・止まり以外）は、動いているかどうかに関わらず見続ける
  const flowing = eng && !eng.error && !eng.escalation && !["canon_hold", "done"].includes(eng.phase) &&
    (eng.running || eng.phase !== "design" || (eng.design && eng.design.attempts > 0) || Date.now() < (S.watchUntil || 0));
  return Object.keys(S.jobs).length > 0 || (S.snap && S.snap.jobs && S.snap.jobs.length > 0) || flowing;
}
setInterval(() => { if (S.slug && busy()) refresh(); }, 4000);

/* ---------------------------------------------------------------- 描画 */
function reached() {
  const st = S.snap ? S.snap.stage : "theme";
  let i = STAGES.indexOf(st);
  if (st === "brief") i = STAGES.indexOf("fix"); // BRIEF ができたら確定に進める
  return i;
}

function render() {
  const snap = S.snap;
  // 隠れた画面の中身は消す（同じ案が二つの画面に描かれて id が重ならないように）
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = true; if (p.id !== "empty") p.innerHTML = ""; });
  renderMeter();
  if (!snap) {
    if (S.view === "theme") { $("#panel-theme").hidden = false; renderTheme(); }
    else $("#empty").hidden = false;
    renderRail();
    return;
  }
  renderRail();
  const v = S.view || snap.stage;
  const el = $(`#panel-${v}`);
  el.hidden = false;
  ({ theme: renderTheme, propose: renderPropose, classify: renderClassify, brief: renderBrief, fix: renderFix,
     canon: renderCanon, write: renderWrite, finish: renderFinish })[v]();
}

function renderRail() {
  const r = S.snap ? reached() : 0;
  const fixed = S.snap && S.snap.fixed;
  document.querySelectorAll("#rail li").forEach((li) => {
    const i = STAGES.indexOf(li.dataset.stage);
    li.className = "";
    if (i < r) li.classList.add("done");
    if (i === r) li.classList.add("now");
    if (!S.snap ? i > 0 : (i > r && !(fixed && i <= STAGES.indexOf("finish")) )) li.classList.add("locked");
    if (fixed && i >= STAGES.indexOf("canon")) li.classList.remove("locked");
    if (li.dataset.stage === (S.view || (S.snap && S.snap.stage))) li.classList.add("view");
  });
}

function renderMeter() {
  const c = S.snap && S.snap.costs;
  $("#meterTotal").textContent = usd(c ? c.total_usd : 0);
  if (!c) { $("#meterTip").textContent = "作品を選ぶと、工程ごとの費用が出ます。"; return; }
  const lines = ["企画（studio）"];
  for (const [k, v] of Object.entries(c.studio)) lines.push(`  ${k.padEnd(14)} ${usd(v.usd)}  ${v.calls}回`);
  lines.push(`  小計 ${usd(c.studio_usd)}`, "", "執筆（engine）");
  for (const [k, v] of Object.entries(c.engine)) lines.push(`  ${k.padEnd(14)} ${usd(v.usd)}  ${v.calls}回`);
  lines.push(`  小計 ${usd(c.engine_usd)}`);
  if (S.snap.fixed && S.snap.fixed.budget) lines.push("", `予算 $${S.snap.fixed.budget}`);
  $("#meterTip").textContent = lines.join("\n");
}

function busyLine(kinds) {
  const mine = Object.values(S.jobs).filter((l) => !kinds || kinds.some((k) => l.startsWith(k)));
  const srv = (S.snap && S.snap.jobs || []).filter((j) => !kinds || kinds.includes(j.kind));
  if (!mine.length && !srv.length) return "";
  const txt = mine.length ? mine.join("、") : srv.map((j) => `${j.kind}（${j.since}秒）`).join("、");
  return `<p class="busy">${h(txt)}を実行中です。このページを離れても止まりません。</p>`;
}

function modelOptions(selected, filter) {
  const list = S.models.length ? S.models : [{ id: selected }];
  return list.filter((m) => !filter || m.id === selected || m.id.includes(filter))
    .map((m) => `<option value="${h(m.id)}" ${m.id === selected ? "selected" : ""}>${h(m.id)}${m.in_per_m != null ? `　入${m.in_per_m}/出${m.out_per_m}` : ""}</option>`).join("");
}

function modelSelect(id, selected) {
  return `<select id="${id}">${modelOptions(selected)}</select>`;
}

/* ---------------------------------------------------------------- 一 テーマ */
function themeForm(t = {}) {
  const v = (k, d = "") => h(t[k] ?? d);
  return `<div class="form">
    <label class="f">ジャンル<input type="text" name="genre" list="genres" value="${v("genre")}" placeholder="SF／ミステリ／ファンタジー／青春…"></label>
    <datalist id="genres"><option>SF</option><option>現代ミステリ</option><option>骨太なファンタジー</option><option>青春・家族</option><option>ホラー</option><option>歴史</option></datalist>
    <label class="f">想定読者<input type="text" name="audience" value="${v("audience", "一般読者")}" placeholder="例：中学生、ハードSFの読者"></label>
    <label class="f">結末の型<input type="text" name="ending" list="endings" value="${v("ending")}" placeholder="例：ビターエンド"></label>
    <datalist id="endings"><option>ハッピーエンド</option><option>ビターエンド</option><option>メリーバッドエンド</option><option>バッドエンド</option><option>開いた結末</option></datalist>
    <label class="f wide">テーマ・題材<textarea name="theme" placeholder="何を書きたいか。一行でも、長くても">${v("theme")}</textarea></label>
    <label class="f">章の数<input type="number" name="chapters" min="1" max="60" value="${v("chapters", 8)}"></label>
    <label class="f">1章の字数<input type="number" name="chapter_target" min="500" max="20000" step="100" value="${v("chapter_target", 3000)}"></label>
    <label class="f">長さのメモ<input type="text" name="length" value="${v("length")}" placeholder="例：2万字前後の短編"></label>
    <label class="f wide">必ず入れたいこと<textarea name="must">${v("must")}</textarea></label>
    <label class="f wide">入れたくないこと<textarea name="must_not">${v("must_not")}</textarea></label>
    <label class="f wide">雰囲気・参考<textarea name="mood">${v("mood")}</textarea></label>
  </div>`;
}

function readThemeForm(root) {
  const t = {};
  root.querySelectorAll("[name]").forEach((el) => { if (el.name !== "slug") t[el.name] = el.type === "number" ? Number(el.value) : el.value.trim(); });
  return t;
}

function renderTheme() {
  const el = $("#panel-theme");
  if (!S.snap) {
    el.innerHTML = `<header><h1>新しい作品</h1></header>
      <label class="f" style="max-width:320px">作品フォルダの名前（英数字・-・_）<input type="text" name="slug" id="slugIn" placeholder="ice-moon"></label>
      <h2>テーマ</h2>${themeForm()}
      <div class="row"><button class="act" id="createBtn" type="button">作品を作って、提案へ進む</button></div>`;
    $("#createBtn").onclick = () => guard(async () => {
      const slug = $("#slugIn").value.trim();
      const d = await api("POST", "/api/works", { slug, theme: readThemeForm(el) });
      S.slug = d.slug; S.view = "propose";
      await loadWorks(S.slug); await refresh();
    });
    return;
  }
  const fixed = !!S.snap.fixed;
  el.innerHTML = `<header><h1>テーマ</h1><span class="muted">版 ${S.snap.theme_version}${fixed ? "（確定済み。変更できません）" : ""}</span></header>
    ${themeForm(S.snap.theme)}
    <div class="row"><button class="ghost" id="retheme" type="button" ${fixed ? "disabled" : ""}>テーマを変えて、最初からやり直す</button>
    <span class="muted">いまの提案・分類・BRIEF は作品フォルダの studio/history/ に残ります。</span></div>`;
  $("#retheme").onclick = () => guard(async () => {
    if (!confirm("テーマを変えると、提案からやり直しになります。よろしいですか。")) return;
    await api("POST", `/api/works/${S.slug}/theme`, { theme: readThemeForm(el) });
    S.view = "propose"; await refresh(); toast("テーマを変えました。提案からやり直します。");
  });
}

/* ---------------------------------------------------------------- 二 提案 */
function sheet(p, opts = {}) {
  const c = S.snap.classify && S.snap.classify.labels && S.snap.classify.labels[p.id];
  const sel = S.snap.selected === p.id;
  const origin = p.origin === "human" ? `<span class="tag human">人が編集（${h(p.parent)}から）</span>` :
    p.origin === "revise" ? `<span class="tag human">改稿（${h(p.parent)}から）</span>` : "";
  const dl = ["setting", "highlight", "appeal", "must_keep", "genre_reason"].map((f) => p[f] ? `<dt>${FIELDS_JA[f]}</dt><dd>${h(p[f])}</dd>` : "").join("") +
    (p.changes ? `<dt>改稿で変えたこと</dt><dd>${h(p.changes)}</dd>` : "") + (p.instruction ? `<dt>改稿の指示</dt><dd>${h(p.instruction)}</dd>` : "");
  const fixed = !!S.snap.fixed;
  return `<article class="sheet${sel ? " selected" : ""}${p.hidden ? " hidden-p" : ""}" id="sheet-${p.id}" data-pid="${p.id}">
    ${c ? `<div class="stamp k-${h(c.label)}" title="${c.nearest ? `いちばん近い案 ${h(c.nearest)}（${c.sim}）` : ""}">${h(c.label).replace("バリエーション", "バリエー<br>ション")}</div>` : ""}
    <div class="meta"><b>${p.id}</b><span>${h(short(p.model))}</span><span>${usd(p.usd)}</span><span>${h(p.kind)}</span><span>${h(p.genre)}</span>${origin}${sel ? "<b>選択中</b>" : ""}</div>
    <h3>${h(p.title)}</h3>
    <p class="syn">${h(p.synopsis)}</p>
    <dl>${dl}</dl>
    ${opts.noActs ? "" : `<div class="acts">
      <button class="ghost small" data-a="open" type="button">全文</button>
      <button class="ghost small" data-a="edit" type="button" ${fixed ? "disabled" : ""}>自分で直す</button>
      <button class="ghost small" data-a="revise" type="button" ${fixed ? "disabled" : ""}>指示して改稿</button>
      <button class="act small" data-a="select" type="button" ${fixed ? "disabled" : ""}>この案で BRIEF へ</button>
      <button class="ghost small" data-a="hide" type="button" ${fixed ? "disabled" : ""}>${p.hidden ? "戻す" : "外す"}</button>
    </div>`}
  </article>`;
}

function bindSheets(root) {
  root.querySelectorAll(".sheet .acts button").forEach((b) => {
    const pid = b.closest(".sheet").dataset.pid;
    b.onclick = () => guard(() => sheetAction(b.dataset.a, pid, b));
  });
}

async function sheetAction(a, pid, btn) {
  const p = S.snap.proposals[pid];
  if (a === "open") { btn.closest(".sheet").classList.toggle("open"); return; }
  if (a === "select") { await api("POST", `/api/works/${S.slug}/select`, { pid }); S.view = "brief"; await refresh(); return; }
  if (a === "hide") { await api("POST", `/api/works/${S.slug}/hide`, { pid, hidden: !p.hidden }); await refresh(); return; }
  if (a === "edit") {
    const f = await dialog(`<h2>${pid} を自分で直す</h2><p class="muted">元の案は残り、直した版が新しい案として加わります。</p>
      ${Object.keys(FIELDS_JA).map((k) => `<label class="f">${FIELDS_JA[k]}<textarea name="${k}" rows="${k === "synopsis" ? 7 : 3}">${h(p[k])}</textarea></label>`).join("")}`, "新しい案として保存");
    if (!f) return;
    const d = await api("POST", `/api/works/${S.slug}/edit`, { pid, fields: f });
    toast(`${d.pid} として保存しました`); await refresh(); return;
  }
  if (a === "revise") {
    const f = await dialog(`<h2>${pid} を改稿してもらう</h2>
      <label class="f">モデル<select name="model">${modelOptions(p.model)}</select></label>
      <label class="f">指示<textarea name="instruction" rows="5" placeholder="例：留まっても汚染は止まらない点を解く。主題からは逃げない"></textarea></label>`, "改稿を頼む");
    if (!f || !f.instruction.trim()) return;
    const d = await api("POST", `/api/works/${S.slug}/revise`, { pid, model: f.model, instruction: f.instruction });
    const r = await waitJob(d.job, `${pid} の改稿`);
    toast(`${r} として届きました`);
  }
}

function dialog(inner, okLabel) {
  return new Promise((resolve) => {
    const dlg = $("#dlg"), form = $("#dlgForm");
    form.innerHTML = inner + `<div class="row"><button class="act" value="ok" type="submit">${h(okLabel)}</button><button class="ghost" value="cancel" type="submit">やめる</button></div>`;
    dlg.onclose = () => {
      if (dlg.returnValue !== "ok") return resolve(null);
      const out = {};
      form.querySelectorAll("[name]").forEach((el) => (out[el.name] = el.value));
      resolve(out);
    };
    dlg.showModal();
  });
}

function renderPropose() {
  const snap = S.snap, el = $("#panel-propose");
  const fixed = !!snap.fixed;
  const def = S.settings.propose_models || [];
  const lastModels = snap.rounds.length ? snap.rounds[snap.rounds.length - 1].models : def;
  const rounds = [...snap.rounds].reverse();
  const roundCost = (r) => r.proposals.reduce((a, pid) => a + (snap.proposals[pid] ? snap.proposals[pid].usd : 0), 0);
  const extra = Object.values(snap.proposals).filter((p) => p.origin !== "model");
  el.innerHTML = `<header><h1>提案</h1><span class="muted">案 ${Object.keys(snap.proposals).length} 件　ラウンド ${snap.rounds.length}</span></header>
    ${fixed ? `<p class="notice">確定済みです。提案は読むだけです。</p>` : `
    <div class="cols">
      <div>
        <label class="f">モデルを探す<input type="text" id="mFilter" placeholder="haiku, luna, deepseek…"></label>
        <div class="models" id="mList"></div>
        <p class="muted">単価は 100万トークンあたりのドル（入力／出力）。</p>
      </div>
      <div>
        <div class="form">
          <label class="f">1モデルあたりの案の数<select id="pN"><option>1</option><option selected>2</option><option>3</option><option>4</option></select></label>
          <label class="f">発散の度合い（temperature）<input type="number" id="pT" min="0" max="1.5" step="0.1" value="0.8"></label>
          <label class="f wide">追加の指示（任意。再提出のときの注文もここに）<textarea id="pI" placeholder="例：舞台を宇宙船にしない／もっと日常に寄せる"></textarea></label>
        </div>
        <div class="row">
          <button class="act" id="pGo" type="button">${snap.rounds.length ? "もう一度出してもらう" : "提案を出してもらう"}</button>
          <button class="ghost" id="pRoll" type="button" ${snap.avoid.length ? "" : "disabled"} title="分類で「収束」になった骨格を渡し、その外の案だけを求めます">収束した骨格を避けて出してもらう</button>
          <button class="ghost" id="pTheme" type="button">テーマを変える</button>
        </div>
        ${snap.avoid.length ? `<details><summary class="muted">避ける骨格（${snap.avoid.length}）</summary><ul>${snap.avoid.map((x) => `<li>${h(x)}</li>`).join("")}</ul></details>` : ""}
        ${busyLine(["提案", "改稿", "propose", "revise"])}
      </div>
    </div>`}
    ${rounds.map((r) => `<h2>${r.id}　${r.mode === "rolling" ? "収束を避けて" : r.mode === "resubmit" ? "再提出" : "最初の提案"}<span class="muted">　${r.models.length}モデル・${usd(roundCost(r))}${r.instruction ? `・指示「${h(r.instruction)}」` : ""}</span></h2>
      ${Object.keys(r.errors || {}).length ? `<p class="notice">返ってこなかったモデル：${Object.entries(r.errors).map(([m, e]) => `${h(short(m))}（${h(e).slice(0, 160)}）`).join("、")}</p>` : ""}
      <div class="sheets">${r.proposals.map((pid) => snap.proposals[pid]).filter(Boolean).map((p) => sheet(p)).join("")}</div>`).join("")}
    ${extra.length ? `<h2>直した案・改稿した案</h2><div class="sheets">${extra.map((p) => sheet(p)).join("")}</div>` : ""}
    ${snap.rounds.length ? `<div class="row"><button class="act" id="toClassify" type="button">分類へ進む</button></div>` : ""}`;
  bindSheets(el);
  if ($("#toClassify")) $("#toClassify").onclick = () => { S.view = "classify"; render(); };
  if (fixed) return;
  const drawModels = () => {
    const f = $("#mFilter").value.trim();
    const checked = new Set([...el.querySelectorAll("#mList input:checked")].map((x) => x.value));
    const base = checked.size ? checked : new Set(lastModels);
    const list = (S.models.length ? S.models : def.map((id) => ({ id }))).filter((m) => base.has(m.id) || !f || m.id.includes(f)).slice(0, 120);
    $("#mList").innerHTML = list.map((m) => `<label><input type="checkbox" value="${h(m.id)}" ${base.has(m.id) ? "checked" : ""}><span>${h(m.id)}</span><span class="price">${m.in_per_m != null ? `${m.in_per_m}／${m.out_per_m}` : ""}</span></label>`).join("");
  };
  drawModels();
  $("#mFilter").oninput = drawModels;
  const go = (mode) => guard(async () => {
    const models = [...el.querySelectorAll("#mList input:checked")].map((x) => x.value);
    if (!models.length) throw new Error("モデルを一つ以上選んでください");
    const d = await api("POST", `/api/works/${S.slug}/propose`, { models, n: Number($("#pN").value), temperature: Number($("#pT").value), instruction: $("#pI").value, mode });
    const r = await waitJob(d.job, `提案（${models.length}モデル）`);
    toast(`${r.proposals.length} 案が届きました` + (Object.keys(r.errors).length ? `（${Object.keys(r.errors).length} モデルは失敗）` : ""));
  });
  $("#pGo").onclick = () => go(snap.rounds.length ? "resubmit" : "new");
  $("#pRoll").onclick = () => go("rolling");
  $("#pTheme").onclick = () => { S.view = "theme"; render(); };
}

/* ---------------------------------------------------------------- 三 分類 */
function renderClassify() {
  const snap = S.snap, el = $("#panel-classify"), c = snap.classify;
  const props = snap.proposals;
  const fixed = !!snap.fixed;
  const ai = c && c.ai;
  el.innerHTML = `<header><h1>分類</h1><span class="muted">収束・バリエーション・外れ値</span></header>
    <p>モデルに各案の骨格（誰が欺く・何を隠す・どの層で驚かせる・何がひっくり返る・舞台・結末）だけを抜き出させ、似ているかどうかは道具が数えます。AI の審判は重複の判定がぶれるため、判定はルールで行います。AI に読ませた分類も並べて見られます。</p>
    <div class="form">
      <label class="f">やり方<select id="cM"><option value="tool">骨格を数える（既定）</option><option value="both">骨格を数える＋AIにも読ませる</option><option value="ai">AIに読ませるだけ</option></select></label>
      <label class="f">骨格を抜き出すモデル${modelSelect("cModel", (c && c.model) || S.settings.skeleton_model)}</label>
    </div>
    <div class="row"><button class="act" id="cGo" type="button" ${fixed ? "disabled" : ""}>${c ? "新しい案を足して分類し直す" : "分類する"}</button>
      ${c ? `<label class="muted"><input type="checkbox" id="cRefresh"> 骨格を抜き出し直す</label>` : ""}</div>
    ${busyLine(["分類", "classify"])}
    ${c && c.clusters && c.clusters.length ? `
      <div class="slider"><span class="muted">似ているとみなす境目</span><input type="range" id="cTh" min="0.2" max="0.9" step="0.05" value="${c.threshold}"><b id="cThV">${c.threshold}</b>
      <span class="muted">案どうしの近さの平均 ${c.mean_similarity ?? "—"}</span></div>
      <h2>まとまり</h2>
      <div class="clusters">${c.clusters.map((cl) => `<div class="cluster k-${h(cl.label)}">
        <div><b>${h(cl.label)}</b>　<span class="muted">${cl.members.length}案${cl.cohesion != null ? `・まとまりの強さ ${cl.cohesion}` : ""}</span></div>
        ${cl.type ? `<div>型：${h(cl.type)}</div>` : ""}
        <div class="members">${cl.members.map((m) => `<span class="chip" data-pid="${m}" title="${h(props[m] ? props[m].title : "")}">${m} ${h(props[m] ? props[m].title : "").slice(0, 14)}</span>`).join("")}</div>
      </div>`).join("")}</div>
      <h2>いちばん近い組</h2>
      <table><tr><th>案</th><th>案</th><th class="num">近さ</th></tr>${(c.top_pairs || []).map((t) => `<tr><td>${t.a} ${h(props[t.a] ? props[t.a].title : "")}</td><td>${t.b} ${h(props[t.b] ? props[t.b].title : "")}</td><td class="num">${t.sim}</td></tr>`).join("")}</table>` : ""}
    ${ai ? `<h2>AI の読み</h2>${(ai.types || []).length ? `<p>目についた型：${ai.types.map(h).join("／")}</p>` : ""}
      <table><tr><th>案</th><th>道具の分類</th><th>AI の分類</th><th>AI の理由</th></tr>
      ${Object.entries(ai.labels).map(([pid, v]) => `<tr><td>${pid} ${h(props[pid] ? props[pid].title : "")}</td><td>${h(c.labels && c.labels[pid] ? c.labels[pid].label : "—")}</td><td>${h(v.label)}</td><td>${h(v.reason)}</td></tr>`).join("")}</table>` : ""}
    ${c && c.skeletons && Object.keys(c.skeletons).length ? `<details><summary><h2 style="display:inline">骨格の一覧</h2></summary>
      <table><tr><th>案</th><th>誰が欺く</th><th>何を隠す</th><th>層</th><th>何が反転</th><th>舞台</th><th>結末</th><th>一行</th></tr>
      ${Object.entries(c.skeletons).map(([pid, s]) => `<tr><td>${pid}</td><td>${h(s.deceiver)}</td><td>${h(s.hidden)}</td><td>${h(s.layer)}</td><td>${h(s.flip)}</td><td>${h(s.stage)}</td><td>${h(s.ending)}</td><td>${h(s.one_line)}</td></tr>`).join("")}</table></details>` : ""}
    ${c ? `<h2>分類つきの案</h2><div class="sheets">${Object.values(props).filter((p) => !p.hidden).map((p) => sheet(p)).join("")}</div>` : ""}
    ${c ? `<div class="row"><button class="ghost" id="cRoll" type="button" ${snap.avoid.length && !fixed ? "" : "disabled"}>収束した骨格を避けて、もう一度出してもらう</button></div>` : ""}`;
  bindSheets(el);
  el.querySelectorAll(".chip").forEach((ch) => (ch.onclick = () => { const s = $(`#sheet-${ch.dataset.pid}`, el); if (s) { s.classList.add("open"); s.scrollIntoView({ behavior: "smooth", block: "center" }); } }));
  $("#cGo").onclick = () => guard(async () => {
    const d = await api("POST", `/api/works/${S.slug}/classify`, { method: $("#cM").value, model: $("#cModel").value, refresh: $("#cRefresh") ? $("#cRefresh").checked : false });
    await waitJob(d.job, "分類"); toast("分類しました");
  });
  if ($("#cTh")) {
    $("#cTh").oninput = () => ($("#cThV").textContent = $("#cTh").value);
    $("#cTh").onchange = () => guard(async () => { await api("POST", `/api/works/${S.slug}/reclassify`, { threshold: Number($("#cTh").value) }); await refresh(); });
  }
  if ($("#cRoll")) $("#cRoll").onclick = () => { S.view = "propose"; render(); setTimeout(() => $("#pRoll") && $("#pRoll").focus(), 50); };
}

/* ---------------------------------------------------------------- 四 BRIEF */
function md(src) {
  src = String(src || "").replace(/<!--[\s\S]*?-->/g, "");
  const inline = (s) => h(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [], lines = src.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (/^\s*$/.test(l)) continue;
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); continue; }
    if (/^---+\s*$/.test(l)) { out.push("<hr>"); continue; }
    if (l.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) { rows.push(lines[i]); i++; }
      i--;
      const cells = (r) => r.replace(/^\||\|\s*$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r) => !/^\|[\s|:-]+\|?\s*$/.test(r));
      out.push("<table>" + body.map((r, k) => "<tr>" + cells(r).map((c) => (k === 0 ? `<th>${inline(c)}</th>` : `<td>${inline(c)}</td>`)).join("") + "</tr>").join("") + "</table>");
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(l)) {
      const items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, "")); i++; }
      i--;
      out.push("<ul>" + items.map((x) => `<li>${inline(x)}</li>`).join("") + "</ul>");
      continue;
    }
    if (l.startsWith(">")) { out.push(`<blockquote>${inline(l.replace(/^>\s?/, ""))}</blockquote>`); continue; }
    out.push(`<p>${inline(l)}</p>`);
  }
  return out.join("\n");
}

async function renderBrief() {
  const snap = S.snap, el = $("#panel-brief");
  const fixed = !!snap.fixed;
  const sel = snap.selected && snap.proposals[snap.selected];
  const briefs = snap.briefs || [];
  if (!S.briefV || !briefs.find((b) => b.v === S.briefV)) S.briefV = fixed ? snap.fixed.brief_v : (briefs.length ? briefs[briefs.length - 1].v : null);
  const cur = briefs.find((b) => b.v === S.briefV);
  el.innerHTML = `<header><h1>BRIEF</h1><span class="muted">執筆指示書。物語の構造（章の機能・伏線の配置・守らせたいこと）だけを決めます</span></header>
    ${!sel ? `<p class="notice">提案か分類の画面で「この案で BRIEF へ」を押してください。</p>` : `
    <h2>選んだ案</h2><div class="sheets" style="grid-template-columns:1fr">${sheet(sel, { noActs: true })}</div>
    ${fixed ? "" : `<div class="form">
      <label class="f">BRIEF を書くモデル${modelSelect("bModel", S.settings.brief_model)}</label>
      <label class="f wide">追加の指示（任意）<textarea id="bI" placeholder="例：視点人物は一人に絞る／第3章で中間の反転を置く"></textarea></label>
    </div>
    <div class="row"><button class="act" id="bGo" type="button">この案から BRIEF を作る</button></div>`}
    ${busyLine(["BRIEF", "brief"])}`}
    ${briefs.length ? `<div class="cols" style="margin-top:20px">
      <div><h3>版</h3><ul class="versions">${briefs.slice().reverse().map((b) => `<li class="${b.v === S.briefV ? "cur" : ""}"><button type="button" data-v="${b.v}">
        v${b.v}　${b.origin === "human" ? "人が編集" : h(short(b.model))}${b.base ? `（v${b.base}から）` : ""}<br><span class="muted">${usd(b.usd)}　${b.lint.ok ? '<span class="lint-ok">検査OK</span>' : `<span class="lint-ng">指摘${b.lint.errors.length}</span>`}${fixed && snap.fixed.brief_v === b.v ? "　確定" : ""}</span></button></li>`).join("")}</ul></div>
      <div id="bView"></div></div>` : ""}`;
  if ($("#bGo")) $("#bGo").onclick = () => guard(async () => {
    const d = await api("POST", `/api/works/${S.slug}/brief`, { model: $("#bModel").value, instruction: $("#bI").value });
    S.briefV = await waitJob(d.job, "BRIEF の作成"); toast(`BRIEF v${S.briefV} ができました`); await refresh();
  });
  el.querySelectorAll(".versions button").forEach((b) => (b.onclick = () => { S.briefV = Number(b.dataset.v); S.briefTab = "read"; renderBrief(); }));
  if (!cur) return;
  const view = $("#bView");
  const lint = cur.lint;
  view.innerHTML = `<div class="row"><button class="ghost small" data-t="read" type="button">読む</button><button class="ghost small" data-t="edit" type="button" ${fixed ? "disabled" : ""}>自分で直す（JSON）</button>
    <span class="muted">v${cur.v}　${h(cur.ts)}${cur.instruction ? `　指示「${h(cur.instruction)}」` : ""}${cur.changes ? `　変更：${h(cur.changes)}` : ""}</span></div>
    ${lint.errors.length ? `<div class="alarm"><h3>機械検査の指摘</h3><ul>${lint.errors.map((e) => `<li>${h(e)}</li>`).join("")}</ul><p class="muted">この版は確定できません。指示欄を空のまま「作り直す」と、指摘を直すよう頼めます。</p></div>` : `<div class="ok-box lint-ok">機械検査OK（章立てと伏線の配置が食い違っていません）</div>`}
    ${lint.warnings.length ? `<div class="notice">${lint.warnings.map(h).join("<br>")}</div>` : ""}
    <div id="bBody"><p class="busy">読み込み中</p></div>
    ${fixed ? "" : `<div class="form" style="margin-top:14px"><label class="f wide">この版に指示を足して作り直す<textarea id="bR" placeholder="例：第2章の緊張を上げる／F3 の回収を第7章に"></textarea></label></div>
    <div class="row"><button class="ghost" id="bRe" type="button">指示を足して作り直す</button>${lint.ok ? `<button class="act" id="bFix" type="button">この版で確定へ進む</button>` : ""}</div>`}`;
  view.querySelectorAll("[data-t]").forEach((b) => (b.onclick = () => { S.briefTab = b.dataset.t; renderBrief(); }));
  if ($("#bRe")) $("#bRe").onclick = () => guard(async () => {
    const d = await api("POST", `/api/works/${S.slug}/brief`, { model: $("#bModel") ? $("#bModel").value : undefined, instruction: $("#bR").value, base: cur.v });
    S.briefV = await waitJob(d.job, "BRIEF の作り直し"); toast(`BRIEF v${S.briefV} ができました`); await refresh();
  });
  if ($("#bFix")) $("#bFix").onclick = () => { S.view = "fix"; render(); };
  if (S.briefTab === "edit" && !fixed) {
    $("#bBody").innerHTML = `<textarea class="code" id="bJson">${h(JSON.stringify(cur.brief, null, 1))}</textarea>
      <div class="row"><button class="act" id="bSave" type="button">新しい版として保存</button><span class="muted">保存すると機械検査が走ります。</span></div>`;
    $("#bSave").onclick = () => guard(async () => {
      let obj;
      try { obj = JSON.parse($("#bJson").value); } catch (e) { throw new Error("JSON として読めません：" + e.message); }
      const d = await api("POST", `/api/works/${S.slug}/brief_edit`, { v: cur.v, brief: obj });
      S.briefV = d.v; S.briefTab = "read"; toast(`v${d.v} として保存しました`); await refresh();
    });
    return;
  }
  const r = await api("GET", `/api/works/${S.slug}/brief/${cur.v}`).catch((e) => ({ error: e.message }));
  $("#bBody").innerHTML = r.markdown ? `<div class="doc">${md(r.markdown)}</div>` : `<pre class="log">${h(JSON.stringify(cur.brief, null, 1))}</pre>`;
}

/* ---------------------------------------------------------------- 五 確定 */
function renderFix() {
  const snap = S.snap, el = $("#panel-fix");
  const f = snap.fixed;
  if (f) {
    el.innerHTML = `<header><h1>確定</h1></header>
      <div class="ok-box"><p><b>${h(f.ts)}</b> に確定しました。案 ${h(f.proposal)}「${h(snap.proposals[f.proposal] ? snap.proposals[f.proposal].title : "")}」、BRIEF v${f.brief_v}（指紋 ${h(f.brief_sha)}）。</p>
      <table>${Object.entries(f.roles).map(([k, v]) => `<tr><th>${ROLE_JA[k] || k}</th><td>${h(typeof v === "string" ? v : v.model)}</td></tr>`).join("")}${f.budget ? `<tr><th>予算</th><td>$${h(f.budget)}</td></tr>` : ""}</table></div>
      <p>ここから先は作品フォルダ works/${h(S.slug)}/ が執筆エンジンの作業場所です。ハーネス（HermesAgent など）に進行を任せることもできます（harness/ を参照）。</p>
      <div class="row"><button class="act" id="toCanon" type="button">CANON へ進む</button></div>`;
    $("#toCanon").onclick = () => { S.view = "canon"; render(); };
    return;
  }
  const ok = snap.briefs.filter((b) => b.lint.ok);
  const roles = S.settings.roles || {};
  el.innerHTML = `<header><h1>確定</h1><span class="muted">提案と BRIEF を確定し、執筆に入ります</span></header>
    ${!ok.length ? `<p class="notice">機械検査を通った BRIEF がまだありません。</p>` : `
    <div class="form">
      <label class="f">BRIEF の版<select id="fV">${ok.slice().reverse().map((b) => `<option value="${b.v}" ${b.v === S.briefV ? "selected" : ""}>v${b.v}（${h(b.origin === "human" ? "人が編集" : short(b.model))}）</option>`).join("")}</select></label>
      <label class="f">予算（ドル。空なら上限なし）<input type="number" id="fB" min="0" step="0.5" placeholder="例 3"></label>
    </div>
    <h2>役ごとのモデル</h2>
    <p class="muted">書く・照合するのは安いモデルで何度でも。全体の整合を直す・校正は上位モデルで一回だけ。止めるかどうかは人が決めます（グラウヴァル第6回の実績では、上位モデルの出番は設計の修正2回・章の手直し5回・校正1回、残りの約90回は安いモデル）。</p>
    <div class="form">${Object.keys(ROLE_JA).map((k) => `<label class="f">${ROLE_JA[k]}${modelSelect("role-" + k, roles[k])}</label>`).join("")}</div>
    <div class="row"><button class="seal" id="fGo" type="button">確定</button><span class="muted">確定すると、この作品のテーマ・提案・BRIEF は変えられなくなります。</span></div>`}`;
  if ($("#fGo")) $("#fGo").onclick = () => guard(async () => {
    const v = Number($("#fV").value);
    if (!confirm(`案 ${snap.selected} と BRIEF v${v} で確定します。よろしいですか。`)) return;
    const rolesSel = {}; Object.keys(ROLE_JA).forEach((k) => (rolesSel[k] = $("#role-" + k).value));
    await api("POST", `/api/works/${S.slug}/fix`, { v, roles: rolesSel, budget: $("#fB").value ? Number($("#fB").value) : null });
    toast("確定しました"); S.view = "canon"; await refresh();
  });
}

/* ---------------------------------------------------------------- 六 CANON・七 執筆 */
const ESC_JA = {
  DESIGN_EXHAUSTED: "CANON の設計が規定回数で審査に通りません",
  CHAPTER_EXHAUSTED: "章が書き直しても審査に通りません",
  LEDGER_FAILED: "章の記録（LEDGER）が書式を満たしません",
  CANON_MODIFIED: "ロックしたあとで CANON が変わりました",
  GUARD_STOP: "見張り番が止めました（STOP か保護ファイルの変更）",
  FINAL_REVIEW_FAILED: "最終審査で重大な指摘があります",
  BUDGET_EXCEEDED: "予算に達しました",
  CALL_FAILED: "モデルの呼び出しに失敗しました",
  CALL_TIMEOUT: "モデルの呼び出しが時間切れになりました",
};

function engineBox() {
  const e = S.snap.engine || {};
  if (e.error) return `<div class="alarm"><h3>エンジンの状態を読めません</h3><p>${h(e.error)}</p></div>`;
  let s = "";
  if (S.snap.tools_outdated) {
    s += `<div class="notice">この作品の道具（tools/）は古い版です。上位モデルの手直しでモデルを選ぶ・指示を足す、表紙を作る、には道具を最新にしてください（プロンプト・BRIEF・CANON・本文は変わりません）。
      <button class="ghost small" data-e="upgrade" type="button">道具を最新にする</button></div>`;
  }
  if (e.running) {
    const f = e.inflight;
    s += `<p class="busy">執筆エンジンが動いています${f ? `（${h(f.task)}・試行${f.attempt}・推論${f.reasoning_chars}字・本文${f.content_chars}字）` : ""}。画面を閉じても止まりません。</p>`;
  }
  if (e.escalation) {
    const x = e.escalation;
    const n = x.chapter;
    const opts = {
      DESIGN_EXHAUSTED: `<button class="act" data-e="rescue_canon" type="button">${(e.canon_rescues || []).length ? "別のモデルで CANON 全体を直す" : "上位モデルで CANON 全体を一度だけ直す"}</button><button class="ghost" data-e="redesign" type="button">指示を足して設計をやり直す</button><button class="ghost" data-e="approve_view" type="button">CANON を読んで、このまま承認する</button>`,
      CHAPTER_EXHAUSTED: `<button class="act" data-e="rescue_chapter" data-n="${n}" type="button">${rescueHist(n).length ? `別のモデルで第${n}章を直す` : `上位モデルで第${n}章の指摘箇所を直す`}</button><button class="ghost" data-e="resolve" data-f="retry" type="button">書き手にもう3稿書かせる</button><button class="ghost" data-e="resolve" data-f="accept" type="button">原稿のまま採用する</button>`,
      FINAL_REVIEW_FAILED: `<button class="act" data-e="rescue_chapter" data-n="${n}" data-pick="1" type="button">上位モデルで章を直す（最終審査の指摘を直す）</button><button class="ghost" data-e="resolve" data-f="done" type="button">原稿のまま完成とする</button>`,
      CALL_FAILED: `<button class="ghost" data-e="resolve" type="button">止まった状態を解いて、もう一度</button>`,
      CALL_TIMEOUT: `<button class="ghost" data-e="resolve" type="button">止まった状態を解いて、もう一度</button>`,
    }[x.code] || `<span class="muted">作品フォルダで対応してから「止まった状態を解く」を押してください。</span><button class="ghost" data-e="resolve" type="button">止まった状態を解く</button>`;
    const hist = x.code === "DESIGN_EXHAUSTED" ? (e.canon_rescues || []) : x.code === "CHAPTER_EXHAUSTED" ? rescueHist(n) : [];
    s += `<div class="alarm"><h3>止まっています：${h(ESC_JA[x.code] || x.code)}</h3><p>${h(x.detail)}</p>${histHtml(hist)}<p class="muted">${h(x.hint)}</p><div class="row">${opts}</div></div>`;
  }
  return s;
}

function rescueHist(n) {
  const c = ((S.snap.engine || {}).chapters || []).find((x) => x.n === Number(n));
  return (c && c.rescues) || [];
}

function histHtml(hist) {
  if (!hist.length) return "";
  return `<p><b>これまでの上位モデルの手直し</b></p><ol class="hist">${hist.map((r) =>
    `<li>${h(short(r.model))}：${h(r.result)}${r.note ? `<br><span class="muted">指示「${h(r.note)}」</span>` : ""}</li>`).join("")}</ol>`;
}

async function rescueDialog(a, extra) {
  const e = S.snap.engine || {};
  const premium = e.premium_model || "";
  const isCh = a === "rescue_chapter";
  const n = extra.n || e.current;
  const hist = isCh ? rescueHist(n) : (e.canon_rescues || []);
  const tried = new Set(hist.map((r) => r.model));
  const list = S.models.length ? S.models.map((m) => m.id) : [premium];
  const def = !tried.has(premium) ? premium : "";
  const f = await dialog(`<h2>${isCh ? "上位モデルで章を直す" : "上位モデルで CANON 全体を直す"}</h2>
    <p class="muted">選んだモデルを一回だけ呼びます（費用がかかります）。直したあと、審査役がもう一度だけ審査します。${isCh ? "直すのは指摘の箇所と前後の一、二文だけです。" : ""}</p>
    ${histHtml(hist)}
    ${isCh && extra.pick ? `<label class="f">直す章<input type="number" name="n" min="1" max="${(e.chapters || []).length}" value="${n}"></label>` : ""}
    <label class="f">モデル${tried.size ? "（試したモデルには印）" : ""}<select name="model">
      ${def ? "" : `<option value="" selected disabled>モデルを選んでください</option>`}
      ${list.map((id) => `<option value="${h(id)}" ${id === def ? "selected" : ""}>${h(id)}${id === premium ? "（指定の上位モデル）" : ""}${tried.has(id) ? "　✓ 試した" : ""}</option>`).join("")}
    </select></label>
    <label class="f">指示（任意。直し方の手がかり）<textarea name="note" rows="4" placeholder="例：伏線 F2 の回収は第2章の小道具を呼び戻す形で。新しい出来事は足さない"></textarea></label>`, "このモデルで直す");
  if (!f) return null;
  if (!f.model) { toast("モデルを選んでください", true); return null; }
  return { ...extra, model: f.model === premium ? "" : f.model, note: f.note, n: isCh ? Number(f.n || n) : undefined, label: short(f.model) };
}

async function engineAct(a, extra = {}) {
  if (a === "approve_view") { S.view = "canon"; render(); return; }
  if (a === "upgrade") {
    const r = await api("POST", `/api/works/${S.slug}/engine`, { action: "upgrade" });
    toast(r.out || r.err, r.rc !== 0); await refresh(); return;
  }
  if (a === "redesign") {
    const f = await dialog(`<h2>指示を足して設計をやり直す</h2><p class="muted">あなたの指示は「プロデューサーの決定」として、最優先の修正指示になります。</p><label class="f">指示<textarea name="feedback" rows="6"></textarea></label>`, "設計をやり直す");
    if (!f || !f.feedback.trim()) return;
    extra.feedback = f.feedback;
  }
  if (a === "rescue_canon" || a === "rescue_chapter") {
    const opt = await rescueDialog(a, extra);
    if (!opt) return;
    extra = opt;
  }
  if (a === "proofread") {
    const m = (S.snap.engine && S.snap.engine.premium_model) || "上位モデル";
    if (!confirm(`${short(m)} を呼びます（費用がかかります）。よろしいですか。`)) return;
  }
  if (a === "rescue_canon" || a === "rescue_chapter" || a === "proofread") {
    const d = await api("POST", `/api/works/${S.slug}/engine`, { action: a, ...extra });
    const r = await waitJob(d.job, { rescue_canon: `上位モデル（${extra.label || ""}）による CANON の修正`, rescue_chapter: `上位モデル（${extra.label || ""}）による第${extra.n}章の修正`, proofread: "上位モデルによる校正" }[a]);
    toast((r.out || r.err || "").trim().split("\n").pop() || "終わりました", r.rc !== 0);
    if (r.rc === 0 && a !== "proofread") await resume();  // 直したら、そのまま続ける
    return;
  }
  if (["start", "polish", "resolve", "redesign"].includes(a)) S.watchUntil = Date.now() + 20000;
  const r = await api("POST", `/api/works/${S.slug}/engine`, { action: a, ...extra });
  toast((r.out || r.err || "").trim().split("\n").pop() || "受け付けました", r.rc !== 0 && r.rc !== 2);
  if (r.rc === 0 && ["redesign", "resolve"].includes(a)) { await resume(); return; }
  await refresh();
}

async function resume() {
  S.watchUntil = Date.now() + 20000;
  await api("POST", `/api/works/${S.slug}/engine`, { action: "start" });
  await refresh();
}

function bindEngine(el) {
  el.querySelectorAll("[data-e]").forEach((b) => (b.onclick = () => guard(() => engineAct(b.dataset.e, {
    n: b.dataset.n ? Number(b.dataset.n) : undefined, flags: b.dataset.f ? [b.dataset.f] : [], pick: !!b.dataset.pick }))));
}

async function renderCanon() {
  const snap = S.snap, el = $("#panel-canon"), e = snap.engine || {};
  if (!snap.fixed) { el.innerHTML = `<header><h1>CANON</h1></header><p class="notice">先に確定してください。</p>`; return; }
  const ph = e.phase;
  const started = ph !== "design" || (e.design && e.design.attempts > 0) || e.running;
  el.innerHTML = `<header><h1>CANON</h1><span class="muted">設定書。書き手が設計し、審査役が審査し、人が承認します</span></header>
    ${engineBox()}
    ${!started ? `<p>書き手（${h(short(snap.fixed.roles.writer.model || snap.fixed.roles.writer))}）が BRIEF から CANON を設計し、審査役が通すまで往復します。通ったらここで止まり、あなたの承認を待ちます。</p>
      <div class="row"><button class="act" data-e="start" type="button">設計を始める</button></div>` : ""}
    ${ph === "design" && !e.running && started && !e.escalation ? `<div class="row"><button class="act" data-e="start" type="button">続ける</button></div>` : ""}
    ${ph === "design" ? `<p class="muted">設計 ${e.design.attempts}/${e.design.max} 回目（${e.design.state === "review" ? "審査待ち" : "設計中"}）　上位モデルの出番 ${e.rescues || 0} 回</p>` : ""}
    ${ph === "canon_hold" ? `<div class="ok-box"><p>CANON が審査を通りました。下を読み、良ければ承認してください。指紋 <span class="fp">${h(snap.canon_fp)}</span></p>
      <div class="row"><button class="seal" id="approve" type="button">承認</button><button class="ghost" data-e="redesign" type="button">指示を足して設計をやり直す</button><button class="ghost" data-e="rescue_canon" type="button">上位モデルに全体を見直させる</button></div></div>` : ""}
    ${["chapters", "final", "done"].includes(ph) ? `<div class="ok-box">承認済み（ロック中）。執筆に進んでいます。<button class="ghost small" id="toWrite" type="button">執筆を見る</button></div>` : ""}
    ${ph === "design" && e.escalation && e.escalation.code === "DESIGN_EXHAUSTED" ? `<div class="row"><button class="seal" id="approve" type="button">このまま承認</button><span class="muted">指紋 <span class="fp">${h(snap.canon_fp || "")}</span></span></div>` : ""}
    <h2>CANON</h2><div id="canonDoc" class="doc"><p class="muted">まだありません。</p></div>
    <h2>審査の指摘</h2><div id="canonFb"></div>`;
  bindEngine(el);
  if ($("#toWrite")) $("#toWrite").onclick = () => { S.view = "write"; render(); };
  if ($("#approve")) $("#approve").onclick = () => guard(async () => {
    if (!confirm(`CANON（指紋 ${snap.canon_fp}）を承認し、執筆を始めます。よろしいですか。`)) return;
    const r = await api("POST", `/api/works/${S.slug}/approve`, { fingerprint: snap.canon_fp });
    toast(r.out.trim().split("\n").pop());
    S.watchUntil = Date.now() + 20000;
    await api("POST", `/api/works/${S.slug}/engine`, { action: "start" });
    S.view = "write"; await refresh();
  });
  const c = await file(`canon/CANON.md`);
  if (c) $("#canonDoc").innerHTML = md(c.text);
  const fb = await file(`work/canon_feedback.md`);
  $("#canonFb").innerHTML = fb ? `<div class="doc">${md(fb.text)}</div>` : `<p class="muted">いま残っている指摘はありません。</p>`;
}

async function renderWrite() {
  const snap = S.snap, el = $("#panel-write"), e = snap.engine || {};
  if (!snap.fixed) { el.innerHTML = `<header><h1>執筆</h1></header><p class="notice">先に確定してください。</p>`; return; }
  const chs = e.chapters || [];
  const total = chs.reduce((a, c) => a + c.chars, 0);
  const canGo = ["chapters", "final"].includes(e.phase) && !e.running && !e.escalation;
  const stJa = { pending: "未着手", drafted: "審査待ち", rewrite: "書き直し", accepted: "記録待ち", done: "確定" };
  el.innerHTML = `<header><h1>執筆</h1><span class="muted">本文 ${total.toLocaleString()} 字　上位モデルの出番 ${e.rescues || 0} 回</span></header>
    ${engineBox()}
    ${e.phase === "design" || e.phase === "canon_hold" ? `<p class="notice">CANON の承認がまだです。</p>` : ""}
    ${canGo ? `<div class="row"><button class="act" data-e="start" type="button">続ける</button></div>` : ""}
    ${e.phase === "done" ? `<div class="ok-box">全章そろい、最終審査を通りました。<button class="ghost small" id="toFinish" type="button">仕上げへ</button></div>` : ""}
    <table><tr><th>章</th><th>状態</th><th class="num">稿</th><th class="num">字数</th><th>メモ</th><th></th></tr>
    ${chs.map((c) => `<tr><td>第${c.n}章</td><td class="st-${c.state}">${stJa[c.state] || c.state}</td><td class="num">${c.attempts}</td><td class="num">${c.chars.toLocaleString()}</td>
      <td class="muted">${c.warnings.map(h).join("<br>")}</td><td>${c.chars ? `<button class="ghost small" data-view="${c.n}" type="button">読む</button>` : ""}</td></tr>`).join("")}</table>
    <p class="muted">伏線：${Object.keys(e.slots || {}).length ? Object.entries(e.slots).map(([k, v]) => `${k} ${({ planted: "設置", reinforced: "補強", recovered: "回収" })[v.state]}（第${v.chapter}章）`).join("　") : "まだ記録なし"}</p>
    <div id="chView"></div>
    <h2>いまの章の指摘</h2><div id="curFb"></div>
    <details><summary>進行の記録（logs/run.log の末尾）</summary><pre class="log" id="runLog"></pre></details>`;
  bindEngine(el);
  if ($("#toFinish")) $("#toFinish").onclick = () => { S.view = "finish"; render(); };
  el.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => { S.chapterView = Number(b.dataset.view); showChapter(); }));
  if (S.chapterView) showChapter();
  const n = String(e.current || 1).padStart(2, "0");
  const fb = e.phase === "done" ? null : await file(`work/ch${n}_feedback.md`);
  $("#curFb").innerHTML = fb ? `<div class="doc">${md(fb.text)}</div>` : `<p class="muted">ありません。</p>`;
  const lg = await file(`logs/run.log`);
  $("#runLog").textContent = lg ? lg.text : "";
}

async function showChapter() {
  const n = String(S.chapterView).padStart(2, "0");
  const c = await file(`chapters/ch${n}.md`);
  const box = $("#chView");
  if (!box) return;
  box.innerHTML = c ? `<h2>第${S.chapterView}章</h2><div class="doc"><pre>${h(c.text)}</pre></div>` : "";
}

/* ---------------------------------------------------------------- 八 仕上げ */
async function renderFinish() {
  const snap = S.snap, el = $("#panel-finish"), e = snap.engine || {};
  if (!snap.fixed) { el.innerHTML = `<header><h1>仕上げ</h1></header><p class="notice">先に確定してください。</p>`; return; }
  const outs = snap.outputs || [];
  const epubs = outs.filter((x) => x.endsWith(".epub"));
  el.innerHTML = `<header><h1>仕上げ</h1><span class="muted">最終レポート・校正・EPUB</span></header>
    ${engineBox()}
    ${e.phase !== "done" ? `<p class="notice">全章の執筆と最終審査が終わってから使えます（いま：${h(e.phase)}）。</p>` : ""}
    <h2>最終レポート</h2><div id="report" class="doc"><p class="muted">まだありません。</p></div>
    <h2>校正</h2>
    <p>上位モデルが各章を読み、直すべき箇所だけを修正案として出します。原稿は変わりません。採るものに印をつけてから当ててください。想定読者（${h(snap.theme.audience || "未指定")}）に合った文体は直さないよう頼んであります。</p>
    <div class="row"><button class="ghost" data-e="polish" type="button" ${e.phase === "done" ? "" : "disabled"}>文体を磨く（任意・書き手のモデル）</button>
      <button class="act" data-e="proofread" type="button" ${e.phase === "done" ? "" : "disabled"}>校正案を出してもらう（上位モデル）</button></div>
    ${busyLine(["上位モデル"])}
    <div id="edits"></div>
    <h2>表紙</h2>
    <div id="coverBox"></div>
    <h2>EPUB</h2>
    <p class="muted">${snap.cover && snap.cover.cover ? `表紙（images/${h(snap.cover.cover)}）と著者名「${h(snap.cover.author || "")}」が入ります。` : "表紙を作ると、EPUB の最初のページと書影に入ります。"}</p>
    <div class="row"><button class="ghost" data-e="epub" type="button" ${e.phase === "done" ? "" : "disabled"}>原稿のまま作る</button>
      <button class="act" id="epubP" type="button" ${outs.includes("final_proofread.md") ? "" : "disabled"}>校正版で作る</button>
      ${epubs.map((x) => `<a href="/api/works/${S.slug}/file?path=${encodeURIComponent("output/" + x)}">${h(x)} をダウンロード</a>`).join("　")}</div>`;
  bindEngine(el);
  $("#epubP").onclick = () => guard(() => engineAct("epub", { proofread: true }));
  renderCover();
  const rep = await file(`output/final_report.md`);
  if (rep) $("#report").innerHTML = md(rep.text);
  const ed = await file(`output/proofread_edits.json`);
  if (!ed) return;
  const edits = JSON.parse(ed.text).edits || [];
  $("#edits").innerHTML = `<table><tr><th>採る</th><th>章</th><th>分類</th><th>原文</th><th>修正</th><th>理由</th></tr>
    ${edits.map((x, i) => `<tr${x.rejected ? ' class="muted"' : ""}><td><input type="checkbox" data-i="${i}" ${x.enabled ? "checked" : ""} ${x.rejected ? "disabled" : ""}></td><td>${x.chapter}</td><td>${h(x.kind)}</td>
      <td>${h(x.before)}</td><td>${h(x.after)}</td><td>${h(x.reason)}${x.rejected ? `<br><b>機械で却下：</b>${h(x.rejected)}` : ""}</td></tr>`).join("")}</table>
    <div class="row"><button class="ghost" id="eSave" type="button">印を保存</button><button class="act" id="eApply" type="button">印をつけた修正を当てる</button></div>`;
  const collect = () => { const m = {}; $("#edits").querySelectorAll("input[data-i]").forEach((c) => (m[c.dataset.i] = c.checked)); return m; };
  $("#eSave").onclick = () => guard(async () => { await api("POST", `/api/works/${S.slug}/edits`, { enabled: collect() }); toast("保存しました"); });
  $("#eApply").onclick = () => guard(async () => {
    await api("POST", `/api/works/${S.slug}/edits`, { enabled: collect() });
    const r = await api("POST", `/api/works/${S.slug}/engine`, { action: "apply_proofread" });
    toast(r.out.trim().split("\n")[0] || "当てました", r.rc !== 0); await refresh();
  });
}

/* ---------------------------------------------------------------- 表紙 */
const COVER_STYLE = { simple: "シンプル", illust: "イラスト", photo: "写実" };
const COVER_FONT = { mincho: "明朝", gothic: "ゴシック", casual: "カジュアル" };
const COVER_DIR = { vertical: "縦書き", horizontal: "横書き" };

function seg(name, options, value) {
  return `<div class="seg" role="radiogroup">${Object.entries(options).map(([k, v]) =>
    `<label><input type="radio" name="${name}" value="${k}" ${k === value ? "checked" : ""}><span>${h(v)}</span></label>`).join("")}</div>`;
}

async function renderCover() {
  const box = $("#coverBox");
  if (!box) return;
  const snap = S.snap, e = snap.engine || {}, cv = snap.cover || {};
  if (!e.canon_fp) { box.innerHTML = `<p class="muted">CANON ができると、題名が決まるので表紙を作れます。</p>`; return; }
  if (snap.tools_outdated) {
    box.innerHTML = `<div class="notice"><p>この作品の道具（tools/）は、表紙の機能より前の版です。道具だけを最新にすると表紙を作れます。プロンプト・BRIEF・CANON・本文は変わりません（記録は notes/INTERVENTIONS.md）。</p>
      <div class="row"><button class="act" id="cvUp" type="button">道具を最新にする</button></div></div>`;
    $("#cvUp").onclick = () => guard(async () => {
      const r = await api("POST", `/api/works/${S.slug}/engine`, { action: "upgrade" });
      toast(r.out || r.err, r.rc !== 0); await refresh();
    });
    return;
  }
  if (!S.imageModels) {
    S.imageModels = [];
    try { S.imageModels = (await api("GET", "/api/image-models")).models; } catch (err) { /* 一覧が取れなくても「使わない」は選べる */ }
  }
  const ids = S.imageModels.map((m) => m.id);
  const pref = (S.settings.cover_models || []).find((m) => ids.includes(m));
  const model = cv.model && (cv.model === "none" || ids.includes(cv.model)) ? cv.model : (pref || ids[0] || "none");
  const ts = encodeURIComponent(cv.ts || "");
  box.innerHTML = `<div class="cover-grid">
    <div class="cover-form">
      <p>画像生成モデルには<b>文字のない絵</b>だけを描かせ、題名と著者名はこちらで組んで重ねます（画像生成モデルは日本語の文字、とくに縦書きを正しく描けないことが多いため）。題名は CANON のものを使います。</p>
      <label class="f">画像生成モデル<select id="cvModel">
        <option value="none" ${model === "none" ? "selected" : ""}>使わない（文字と色だけ・費用なし）</option>
        ${S.imageModels.map((m) => `<option value="${h(m.id)}" ${m.id === model ? "selected" : ""}>${h(m.id)}${m.price ? `　1枚 $${h(m.price)}` : ""}</option>`).join("")}
      </select></label>
      <div class="f-group"><span class="muted">スタイル</span>${seg("cvStyle", COVER_STYLE, cv.style || "illust")}</div>
      <label class="f">モチーフ（描いてほしいもの）<textarea id="cvMotif" rows="3" placeholder="例：霧の中の鐘楼と、手のひらに載る古い鍵">${h(cv.motif || "")}</textarea></label>
      <div class="f-group"><span class="muted">題名</span>${seg("cvDir", COVER_DIR, cv.title_dir || "vertical")}</div>
      <div class="f-group"><span class="muted">題名の書体</span>${seg("cvFont", COVER_FONT, cv.font || "mincho")}</div>
      <label class="f">著者名<input type="text" id="cvAuthor" value="${h(cv.author || "")}" placeholder="EPUB の著者にも入ります"></label>
      <div class="row"><button class="act" id="cvGo" type="button">表紙を作る</button>
        <button class="ghost" id="cvRe" type="button" ${cv.art ? "" : "disabled"} title="前回の絵はそのままに、題名・書体・著者名だけを組み直します（費用なし）">絵はそのまま、文字だけ組み直す</button></div>
      ${busyLine(["表紙"])}
      ${cv.note ? `<p class="notice">${h(cv.note)}</p>` : ""}
    </div>
    <div class="cover-preview">${cv.cover ? `<img src="/api/works/${S.slug}/file?path=${encodeURIComponent("images/" + cv.cover)}&t=${ts}" alt="表紙『${h(cv.title || "")}』">
      <p class="muted">${h(cv.style_ja || "")}・${h(cv.font_ja || "")}・${h(COVER_DIR[cv.title_dir] || "")}<br>${h(cv.model === "none" ? "絵なし" : short(cv.model))}${cv.usd ? `　${usd(cv.usd)}` : ""}</p>` :
      `<div class="cover-empty">まだ表紙はありません</div>`}</div>
  </div>`;
  const val = (n) => (box.querySelector(`input[name=${n}]:checked`) || {}).value;
  const make = (reuse) => guard(async () => {
    const m = $("#cvModel").value;
    if (!reuse && m !== "none" && !confirm(`${short(m)} で絵を描きます（費用がかかります）。よろしいですか。`)) return;
    const d = await api("POST", `/api/works/${S.slug}/engine`, { action: "cover", model: m, style: val("cvStyle"), motif: $("#cvMotif").value,
      title_dir: val("cvDir"), font: val("cvFont"), author: $("#cvAuthor").value, reuse_art: reuse });
    const r = await waitJob(d.job, "表紙の作成");
    toast((r.out || r.err || "").trim().split("\n").pop() || "表紙を作りました", r.rc !== 0);
  });
  $("#cvGo").onclick = () => make(false);
  $("#cvRe").onclick = () => make(true);
}

/* ---------------------------------------------------------------- 起動 */
document.querySelectorAll("#rail button").forEach((b) => (b.onclick = () => {
  const li = b.closest("li");
  if (li.classList.contains("locked")) return;
  S.view = li.dataset.stage; render();
}));
$("#workSel").onchange = async () => { S.slug = $("#workSel").value; S.view = null; S.briefV = null; S.chapterView = null; localStore("slug", S.slug); await refresh(); };
$("#newWorkBtn").onclick = () => { S.slug = ""; S.snap = null; S.view = "theme"; $("#workSel").value = ""; render(); };

function localStore(k, v) {
  try { if (v === undefined) return localStorage.getItem("studio." + k); localStorage.setItem("studio." + k, v); } catch (e) { return null; }
}

(async () => {
  await guard(loadModels);
  await guard(() => loadWorks());
  const last = localStore("slug");
  if (last && [...$("#workSel").options].some((o) => o.value === last)) { S.slug = last; $("#workSel").value = last; }
  await refresh();
})();
