const TOKEN = new URLSearchParams(location.hash.slice(1)).get("t") || "";
const $ = (id) => document.getElementById(id);

/* ---------------------------------------------------------------- api */

async function api(path, body) {
  const res = await fetch("/api" + path, {
    method: body ? "POST" : "GET",
    headers: { "X-AgSwitch-Token": TOKEN, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data;
  try {
    data = await res.json();
  } catch {
    data = { ok: false, message: `HTTP ${res.status}` };
  }
  return data;
}

/* ---------------------------------------------------------------- ui 基件 */

function toast(msg, kind = "ok") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  $("toast").appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

function showModal(title, body) {
  $("modal-title").textContent = title;
  $("modal-body").textContent = body;
  $("modal").hidden = false;
}
$("modal-close").onclick = () => ($("modal").hidden = true);
$("modal").onclick = (e) => { if (e.target.id === "modal") $("modal").hidden = true; };
$("modal-copy").onclick = async () => {
  try {
    await navigator.clipboard.writeText($("modal-body").textContent);
    toast("已复制");
  } catch {
    toast("复制失败", "bad");
  }
};

function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function ago(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return "刚刚";
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`;
  return `${Math.round(s / 86400)} 天前`;
}

/* 把 ISO 时间（UTC）转成本地时间 */
function fmtIso(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function untilIso(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  let s = Math.round((d - Date.now()) / 1000);
  if (s <= 0) return "已重置";
  const dd = Math.floor(s / 86400); s -= dd * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60);
  if (dd > 0) return `${dd}天${h}小时后重置`;
  if (h > 0) return `${h}小时${m}分后重置`;
  return `${m}分钟后重置`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------------------------------------------------------- 额度渲染 */

function barClass(pct) {
  if (pct <= 0) return "crit";
  if (pct <= 15) return "warn";
  if (pct <= 40) return "mid";
  return "ok";
}

function renderQuota(payload) {
  const body = $("quota-body");
  if (!payload || !payload.quota) return;

  const q = payload.quota;
  const acc = payload.account || {};
  $("quota-when").textContent = `更新于 ${ago(payload.fetchedAt || q.fetchedAt)}`;

  let html = "";

  // 账号 + 计划 + credits
  html += `<div class="acct-strip">`;
  html += `<div class="acct-item"><span class="k">账号</span><span class="v">${escapeHtml(acc.email || "—")}</span></div>`;
  html += `<div class="acct-item"><span class="k">计划</span><span class="v plan">${escapeHtml(acc.plan || "—")}</span></div>`;
  if (acc.promptCreditsTotal) {
    html += `<div class="acct-item"><span class="k">Prompt credits</span><span class="v">${acc.promptCreditsLeft ?? "?"} / ${acc.promptCreditsTotal}</span></div>`;
  }
  if (acc.flowCreditsTotal) {
    html += `<div class="acct-item"><span class="k">Flow credits</span><span class="v">${acc.flowCreditsLeft ?? "?"} / ${acc.flowCreditsTotal}</span></div>`;
  }
  html += `</div>`;

  // 各模型组
  for (const g of q.groups || []) {
    html += `<div class="qgroup">`;
    html += `<div class="qgroup-head"><span class="qg-name">${escapeHtml(g.name)}</span></div>`;
    for (const b of g.buckets || []) {
      const pct = b.percent;
      const cls = b.disabled ? "disabled" : barClass(pct);
      const shown = b.disabled ? "不适用" : `${pct}%`;
      html += `<div class="qrow ${b.disabled ? "is-disabled" : ""}" title="${escapeHtml(b.description || "")}">`;
      html += `<div class="qrow-label"><span class="qname">${escapeHtml(b.name)}</span>`;
      html += `<span class="qreset">${b.disabled ? "本周额度用尽" : untilIso(b.resetAt)} · ${fmtIso(b.resetAt)}</span></div>`;
      html += `<div class="qbar"><div class="qfill ${cls}" style="width:${b.disabled ? 0 : pct}%"></div></div>`;
      html += `<div class="qpct ${cls}">${shown}</div>`;
      html += `</div>`;
    }
    html += `</div>`;
  }

  if (q.note) {
    html += `<p class="qnote">${escapeHtml(q.note)}</p>`;
  }
  body.innerHTML = html;
}

async function loadQuota(force = false, btn = null) {
  if (btn) { btn.disabled = true; btn.textContent = "拉取中…"; }
  try {
    const data = await api(force ? "/quota?force=1" : "/quota");
    if (!data.ok) {
      $("quota-body").innerHTML = `<div class="quota-empty bad">${escapeHtml(data.message)}</div>`;
      if (btn) toast(data.message, "bad");
      return;
    }
    if (data.data) renderQuota(data.data);
    if (btn) toast("额度已更新");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "刷新额度"; }
  }
}

/* ---------------------------------------------------------------- 账号列表 */

function renderAccounts(list) {
  const box = $("list");
  box.innerHTML = "";
  $("count").textContent = list.length;
  $("empty").hidden = list.length > 0;

  list.forEach((a, i) => {
    const row = document.createElement("div");
    row.className = "row" + (a.active ? " active" : "");

    const dot = document.createElement("span");
    dot.className = "dot";

    const who = document.createElement("div");
    who.className = "who";

    const name = document.createElement("div");
    name.className = "name";
    const nameText = document.createElement("span");
    nameText.textContent = a.label || a.email || `账号 …${a.fp.slice(-6)}`;
    name.append(nameText);
    if (a.active) {
      const t = document.createElement("span");
      t.className = "tag active-tag";
      t.textContent = "使用中";
      name.append(t);
    }
    if (a.active && !a.isLiveCredential) {
      const t = document.createElement("span");
      t.className = "tag stale";
      t.textContent = "凭据已被换掉";
      t.title = "Antigravity 里的登录态已经不是这条记录的 token 了，点「同步 token」或「重新应用」";
      name.append(t);
    }
    if (!a.hasRefresh) {
      const t = document.createElement("span");
      t.className = "tag norefresh";
      t.textContent = "无 refresh_token";
      name.append(t);
    }

    const meta = document.createElement("div");
    meta.className = "meta";
    const bits = [];
    if (a.email && a.email !== a.label) bits.push(`<b>${escapeHtml(a.email)}</b>`);
    bits.push(`auth ${escapeHtml(a.authMethod || "—")}`);
    bits.push(`到期 ${fmtTime(a.expiry)}${a.expired ? "（需重新登录）" : ""}`);
    bits.push(`抓取于 ${ago(a.capturedAt)}`);
    meta.innerHTML = bits.join(" · ");

    who.append(name, meta);

    const actions = document.createElement("div");
    actions.className = "actions";

    const up = mkBtn("↑", "ghost icon", () => doMove(a.fp, -1), i === 0);
    up.title = "上移";
    const down = mkBtn("↓", "ghost icon", () => doMove(a.fp, 1), i === list.length - 1);
    down.title = "下移";
    const sw = mkBtn(a.active ? "重新应用" : "切换", "primary sm", (b) => doSwitch(a.fp, b));
    const sg = mkBtn("同步 token", "ghost", (b) => doRelogin(b), !a.active);
    sg.title = "把 Antigravity 里当前真实的 token 同步进这个账号";
    const rn = mkBtn("改名", "ghost", () => doRename(a));
    const del = mkBtn("移除", "danger", () => doDelete(a));

    actions.append(up, down, sw, sg, rn, del);
    row.append(dot, who, actions);
    box.appendChild(row);
  });
}

function mkBtn(text, cls, fn, disabled = false) {
  const b = document.createElement("button");
  b.className = cls;
  b.textContent = text;
  b.disabled = !!disabled;
  b.onclick = () => fn(b);
  return b;
}

/* ---------------------------------------------------------------- 状态条 */

function renderStatus(s) {
  const hub = $("pill-hub");
  hub.className = "pill " + (s.hubRunning ? "on" : "off");
  hub.textContent = s.hubRunning ? `Hub 运行中 · ${s.hubPid}` : "Hub 未运行";

  const cred = $("pill-cred");
  if (!s.credPresent) {
    cred.className = "pill warn";
    cred.textContent = "无凭据";
  } else if (s.liveExpired) {
    cred.className = "pill warn";
    cred.textContent = "token 已过期";
  } else {
    cred.className = "pill on";
    cred.textContent = "凭据已就绪";
  }

  $("live-email").textContent = s.credPresent
    ? (s.liveEmail || "已登录（token 不含邮箱）")
    : "未登录";
  $("live-avatar").textContent = (s.liveEmail || "?")[0].toUpperCase();
  $("live-dot").className = "dot-live " + (s.credPresent ? "on" : "off");
  $("live-sub").textContent = s.credPresent
    ? `auth_method ${s.liveAuthMethod || "—"} · access_token 到期 ${s.liveExpiry}` +
      (s.liveExpired ? "（已过期）" : "")
    : "先去 Antigravity 里登录一个账号";

  const ls = s.lsOnline ? `LS 在线 (${s.hubPort})` : "LS 离线";
  $("store-path").textContent = `账号库：${s.storePath}（${s.accountCount} 个）　·　${ls}`;

  const banner = $("banner");
  if (s.storeError) { banner.hidden = false; banner.textContent = s.storeError; }
  else banner.hidden = true;

  const mm = $("mismatch");
  if (s.emailMismatch) {
    mm.hidden = false;
    $("mismatch-text").textContent =
      `Hub 本地标记（app_storage）里还是 ${s.flaggedEmail}，但凭据真实身份已是 ${s.liveEmail}。` +
      `界面/引导页可能显示旧账号，点右侧同步。`;
  } else {
    mm.hidden = true;
  }
}

$("btn-fix-email").onclick = (e) => busy(e.target, async () => {
  const data = await api("/fix-email", {});
  toast(data.message, data.ok ? "ok" : "bad");
  if (data.log && data.log.length) showModal("修正邮箱标记", data.log.join("\n"));
  await refresh();
});

/* ---------------------------------------------------------------- 动作 */

async function busy(btn, fn) {
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try { await fn(); }
  finally { if (btn) { btn.disabled = false; btn.textContent = old; } }
}

async function doSwitch(fp, btn) {
  await busy(btn, async () => {
    const data = await api("/switch", {
      fp,
      clearSession: $("opt-clear").checked,
      restart: $("opt-restart").checked,
    });
    toast(data.message, data.ok ? "ok" : "bad");
    if (data.log && data.log.length) showModal(data.ok ? "切换完成" : "切换失败", data.log.join("\n"));
    await refresh();
    loadQuota(true);
  });
}

async function doRelogin(btn) {
  await busy(btn, async () => {
    const data = await api("/relogin", {});
    toast(data.message, data.ok ? "ok" : "bad");
    await refresh();
  });
}

async function doRename(a) {
  const label = prompt("新的显示名", a.label || a.email || "");
  if (label === null) return;
  const data = await api("/rename", { fp: a.fp, label });
  toast(data.message, data.ok ? "ok" : "bad");
  await refresh();
}

async function doDelete(a) {
  if (!confirm(`从账号库移除「${a.label}」？\n只删本地加密备份，不影响 Antigravity 当前登录。`)) return;
  const data = await api("/delete", { fp: a.fp });
  toast(data.message, data.ok ? "ok" : "bad");
  await refresh();
}

async function doMove(fp, delta) {
  const data = await api("/move", { fp, delta });
  if (data.ok) await refresh();
}

$("btn-capture").onclick = (e) => busy(e.target, async () => {
  const data = await api("/capture", { label: $("capture-label").value });
  toast(data.message, data.ok ? "ok" : "bad");
  if (data.log && data.log.length) showModal("已抓取当前账号", data.log.join("\n"));
  $("capture-label").value = "";
  await refresh();
});

$("btn-glogin").onclick = (e) => busy(e.target, async () => {
  const data = await api("/login", {});
  toast(data.message, data.ok ? "ok" : "bad");
  const lines = [];
  if (data.data && data.data.authUrl) lines.push("授权地址（在浏览器打开）：\n" + data.data.authUrl + "\n");
  if (data.log) lines.push(...data.log);
  showModal(data.ok ? "Google 登录" : "登录失败", lines.join("\n") || data.message);
});

$("btn-quota").onclick = (e) => loadQuota(true, e.target);

$("btn-diagnose").onclick = async () => {
  const data = await api("/diagnose", {});
  const lines = data.log && data.log.length ? data.log : [data.message];
  showModal("体检报告", lines.join("\n"));
};

$("btn-diagnose2").onclick = $("btn-diagnose").onclick;

$("btn-wsl-check").onclick = async (e) => {
  const data = await api("/wsl");
  const lines = data.log && data.log.length ? data.log : [data.message];
  showModal("WSL 状态", lines.join("\n"));
};

$("btn-wsl-clean").onclick = (e) => busy(e.target, async () => {
  const data = await api("/wsl/clean-logs", {});
  toast(data.message, data.ok ? "ok" : "bad");
  if (data.log && data.log.length) showModal("清理日志", data.log.join("\n"));
});

$("btn-wsl-fix").onclick = async () => {
  const data = await api("/wsl/command", {});
  showModal("根治命令（需要管理员权限）", (data.log || [data.message]).join("\n"));
};

$("btn-reload").onclick = () => refresh(true);

/* ---------------------------------------------------------------- 主循环 */

async function refresh(announce = false) {
  const data = await api("/state");
  if (!data.ok || !data.status) {
    if (announce) toast(data.message || "读取失败", "bad");
    return;
  }
  renderStatus(data.status);
  renderAccounts(data.accounts || []);

  // 首次进入 / LS 刚上线时，立刻拉一次额度（不要等用户手点）
  if (data.status.lsOnline && !QUOTA_LOADED) {
    QUOTA_LOADED = true;
    loadQuota(false);
  }
}

let QUOTA_LOADED = false;
refresh();
setInterval(() => refresh(), 12000);
// 额度单独慢轮询，避免和账号状态一起刷屏
setInterval(() => { if (!document.hidden) loadQuota(false); }, 60000);
