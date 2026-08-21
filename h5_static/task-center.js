(function () {
  "use strict";

  var state = { rows: [], open: false, seen: {}, loading: false, dragging: false };
  var brand = (function () {
    try {
      var raw = String(new URLSearchParams(location.search).get("brand") || new URLSearchParams(location.search).get("brand_mark") || "bihuo").trim().toLowerCase();
      return /^[a-z][a-z0-9_-]{0,62}$/.test(raw) ? raw : "bihuo";
    } catch (_) {
      return "bihuo";
    }
  })();
  var storage = function (key) { return key + ":" + brand; };

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }

  function token() {
    return localStorage.getItem(storage("lobster_h5_token")) || (brand === "bihuo" ? localStorage.getItem("lobster_h5_token") || "" : "");
  }

  function installationId() {
    return localStorage.getItem(storage("lobster_h5_selected_installation_id")) || "";
  }

  function headers() {
    var value = token();
    return value ? { Authorization: "Bearer " + value } : {};
  }

  function isActive(row) {
    return ["pending", "processing", "running", "claimed", "queued"].indexOf(String(row && row.status || "").toLowerCase()) >= 0;
  }

  function taskTitle(row) {
    return row.title || row.task_title || row.name || "正在执行的任务";
  }

  function taskMessage(row) {
    var progress = row.progress && typeof row.progress === "object" ? row.progress : {};
    return row.status_text || progress.text || progress.message || row.content || (String(row.status || "").toLowerCase() === "pending" ? "等待设备领取" : "正在执行");
  }

  function mount() {
    if (document.getElementById("lobsterTaskFab")) return;
    var root = document.createElement("div");
    root.id = "lobsterTaskCenter";
    root.innerHTML = [
      '<button id="lobsterTaskFab" class="lobster-task-fab" type="button" aria-label="当前执行任务" title="当前执行任务">',
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5h12M6 12h12M6 19h8"></path><path d="m16 17 2 2 4-5"></path></svg><i></i></button>',
      '<section id="lobsterTaskPanel" class="lobster-task-panel" hidden aria-label="当前执行任务">',
      '<header class="lobster-task-head"><strong>当前执行任务</strong><button class="lobster-task-close" type="button" aria-label="关闭">×</button></header>',
      '<div id="lobsterTaskList" class="lobster-task-list"></div></section>'
    ].join("");
    document.body.appendChild(root);
    var fab = document.getElementById("lobsterTaskFab");
    fab.addEventListener("click", function () {
      if (state.dragging) return;
      state.open = !state.open;
      render();
    });
    root.querySelector(".lobster-task-close").addEventListener("click", function () { state.open = false; render(); });
    root.querySelector("#lobsterTaskList").addEventListener("click", stopRun);
    setupDrag(fab);
  }

  function viewportBounds(fab) {
    var viewport = window.visualViewport;
    var left = Number(viewport && viewport.offsetLeft || 0);
    var top = Number(viewport && viewport.offsetTop || 0);
    var width = Number(viewport && viewport.width || window.innerWidth || 0);
    var height = Number(viewport && viewport.height || window.innerHeight || 0);
    var shell = document.querySelector(".shell");
    var shellRect = shell ? shell.getBoundingClientRect() : { left: left, right: left + width };
    var size = fab.offsetWidth || 58;
    var minLeft = Math.max(left + 10, shellRect.left + 10);
    var maxLeft = Math.max(minLeft, Math.min(left + width - size - 10, shellRect.right - size - 10));
    return { minLeft: minLeft, maxLeft: maxLeft, minTop: top + 10, maxTop: Math.max(top + 10, top + height - size - 10) };
  }

  function placeFab(fab, x, y) {
    var bounds = viewportBounds(fab);
    var left = Math.min(bounds.maxLeft, Math.max(bounds.minLeft, Number(x) || bounds.minLeft));
    var top = Math.min(bounds.maxTop, Math.max(bounds.minTop, Number(y) || bounds.minTop));
    fab.style.right = "auto";
    fab.style.bottom = "auto";
    fab.style.left = Math.round(left) + "px";
    fab.style.top = Math.round(top) + "px";
    positionPanel();
    return { left: left, top: top, bounds: bounds };
  }

  function positionPanel() {
    var fab = document.getElementById("lobsterTaskFab");
    var panel = document.getElementById("lobsterTaskPanel");
    if (!fab || !panel || panel.hidden) return;
    var fabRect = fab.getBoundingClientRect();
    var viewport = window.visualViewport;
    var viewportLeft = Number(viewport && viewport.offsetLeft || 0);
    var viewportTop = Number(viewport && viewport.offsetTop || 0);
    var viewportWidth = Number(viewport && viewport.width || window.innerWidth || 0);
    var viewportHeight = Number(viewport && viewport.height || window.innerHeight || 0);
    var panelWidth = Math.min(390, viewportWidth - 24);
    panel.style.width = panelWidth + "px";
    panel.style.right = "auto";
    panel.style.bottom = "auto";
    var left = fabRect.right - panelWidth;
    left = Math.max(viewportLeft + 12, Math.min(left, viewportLeft + viewportWidth - panelWidth - 12));
    var panelHeight = panel.offsetHeight || 180;
    var above = fabRect.top - panelHeight - 10;
    var below = fabRect.bottom + 10;
    var top = above >= viewportTop + 10 ? above : Math.min(below, viewportTop + viewportHeight - panelHeight - 10);
    panel.style.left = Math.round(left) + "px";
    panel.style.top = Math.max(viewportTop + 10, Math.round(top)) + "px";
  }

  function setupDrag(fab) {
    var key = storage("lobster_h5_task_fab_position");
    var pointerId = null;
    var startX = 0;
    var startY = 0;
    var startLeft = 0;
    var startTop = 0;
    var moved = false;
    var suppressClick = false;

    function restore() {
      var saved = null;
      try { saved = JSON.parse(localStorage.getItem(key) || "null"); } catch (_) {}
      if (!saved || !Number.isFinite(Number(saved.x)) || !Number.isFinite(Number(saved.y))) return;
      var bounds = viewportBounds(fab);
      placeFab(fab, bounds.minLeft + (bounds.maxLeft - bounds.minLeft) * saved.x, bounds.minTop + (bounds.maxTop - bounds.minTop) * saved.y);
    }

    function persist() {
      var rect = fab.getBoundingClientRect();
      var bounds = viewportBounds(fab);
      try {
        localStorage.setItem(key, JSON.stringify({
          x: Math.max(0, Math.min(1, (rect.left - bounds.minLeft) / Math.max(1, bounds.maxLeft - bounds.minLeft))),
          y: Math.max(0, Math.min(1, (rect.top - bounds.minTop) / Math.max(1, bounds.maxTop - bounds.minTop)))
        }));
      } catch (_) {}
    }

    fab.addEventListener("pointerdown", function (event) {
      if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) return;
      var rect = fab.getBoundingClientRect();
      pointerId = event.pointerId;
      startX = event.clientX;
      startY = event.clientY;
      startLeft = rect.left;
      startTop = rect.top;
      moved = false;
      try { fab.setPointerCapture(pointerId); } catch (_) {}
    });
    fab.addEventListener("pointermove", function (event) {
      if (pointerId === null || event.pointerId !== pointerId) return;
      var dx = event.clientX - startX;
      var dy = event.clientY - startY;
      if (!moved && Math.hypot(dx, dy) < 6) return;
      moved = true;
      state.dragging = true;
      fab.classList.add("is-dragging");
      if (event.cancelable) event.preventDefault();
      placeFab(fab, startLeft + dx, startTop + dy);
    });
    function finish(event) {
      if (pointerId === null || event.pointerId !== pointerId) return;
      try { fab.releasePointerCapture(pointerId); } catch (_) {}
      pointerId = null;
      fab.classList.remove("is-dragging");
      if (!moved) return;
      persist();
      suppressClick = true;
      setTimeout(function () { state.dragging = false; }, 0);
      setTimeout(function () { suppressClick = false; }, 500);
    }
    fab.addEventListener("pointerup", finish);
    fab.addEventListener("pointercancel", finish);
    fab.addEventListener("click", function (event) {
      if (!suppressClick) return;
      suppressClick = false;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    window.addEventListener("resize", restore, { passive: true });
    window.visualViewport && window.visualViewport.addEventListener("resize", restore, { passive: true });
    restore();
  }

  function render() {
    var panel = document.getElementById("lobsterTaskPanel");
    var fab = document.getElementById("lobsterTaskFab");
    var list = document.getElementById("lobsterTaskList");
    if (!panel || !fab || !list) return;
    var running = state.rows.filter(isActive);
    fab.classList.toggle("is-active", running.length > 0);
    panel.hidden = !state.open;
    list.innerHTML = running.length ? running.slice(0, 8).map(function (row) {
      return '<article class="lobster-task-card"><div class="lobster-task-copy"><div class="lobster-task-title">' + escapeHtml(taskTitle(row)) + '</div><div class="lobster-task-message">' + escapeHtml(taskMessage(row)) + '</div></div>' + (row.id ? '<button class="lobster-task-stop" data-run-id="' + escapeHtml(row.id) + '" type="button">停止</button>' : '') + '</article>';
    }).join("") : '<div class="lobster-task-empty">当前没有执行中的任务</div>';
    requestAnimationFrame(positionPanel);
  }

  async function refresh() {
    if (state.loading || !token()) return;
    state.loading = true;
    try {
      var params = new URLSearchParams({ limit: "30", compact: "false" });
      var iid = installationId();
      if (iid) params.set("installation_id", iid);
      var response = await fetch("/api/scheduled-tasks/runs?" + params.toString(), { headers: headers() });
      if (!response.ok) throw new Error("load failed");
      var data = await response.json();
      var rows = Array.isArray(data.runs) ? data.runs : [];
      var running = rows.filter(isActive);
      var fresh = running.some(function (row) { return row.id && !state.seen[row.id]; });
      state.seen = {};
      running.forEach(function (row) { if (row.id) state.seen[row.id] = true; });
      state.rows = rows;
      if (fresh) state.open = true;
      render();
    } catch (_) {
      // Keep the last known task state during a transient network failure.
    } finally {
      state.loading = false;
    }
  }

  async function stopRun(event) {
    var button = event.target.closest("[data-run-id]");
    if (!button) return;
    button.disabled = true;
    button.textContent = "停止中";
    try {
      var response = await fetch("/api/scheduled-tasks/runs/" + encodeURIComponent(button.dataset.runId) + "/cancel", { method: "POST", headers: headers() });
      if (!response.ok) throw new Error("stop failed");
      await refresh();
    } catch (_) {
      button.disabled = false;
      button.textContent = "重试";
    }
  }

  function start() {
    mount();
    refresh();
    setInterval(refresh, 4000);
    window.addEventListener("storage", function (event) {
      if (event.key === storage("lobster_h5_selected_installation_id")) refresh();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
