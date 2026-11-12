// PRAT web UI behaviour: CSRF header for htmx, confirmations, live streams,
// keyboard shortcuts, theme switching and elapsed-time counters.
//
// No inline scripts exist (the Content Security Policy forbids them), and no
// text from the server is ever inserted as HTML except journal fragments that
// the server rendered through its autoescaping templates.
(function () {
  "use strict";

  var htmx = window.htmx;

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  // CSRF: every htmx request carries the session's token in a header.
  document.body.addEventListener("htmx:configRequest", function (evt) {
    evt.detail.headers["X-CSRF-Token"] = csrfToken();
  });

  // Confirmations use a native <dialog> instead of window.confirm.
  document.body.addEventListener("htmx:confirm", function (evt) {
    var question = evt.detail.question;
    if (!question) {
      return;
    }
    evt.preventDefault();
    var dialog = document.getElementById("confirm-dialog");
    if (!dialog || typeof dialog.showModal !== "function") {
      if (window.confirm(question)) {
        evt.detail.issueRequest(true);
      }
      return;
    }
    var source = evt.detail.elt;
    var ok = document.getElementById("confirm-ok");
    document.getElementById("confirm-message").textContent = question;
    ok.textContent = (source && source.getAttribute("data-confirm-label")) || "Continue";
    ok.className = "btn " + ((source && source.getAttribute("data-confirm-tone")) === "danger" ? "btn-danger-solid" : "btn-primary");
    dialog.returnValue = "";
    dialog.addEventListener(
      "close",
      function () {
        if (dialog.returnValue === "ok") {
          evt.detail.issueRequest(true);
        }
      },
      { once: true }
    );
    dialog.showModal();
    ok.focus();
  });

  // Theme: system, light or dark; remembered per browser.
  var THEMES = ["system", "light", "dark"];
  var THEME_NAMES = { system: "follow the system", light: "light", dark: "dark" };

  function applyTheme(pref) {
    var root = document.documentElement;
    root.setAttribute("data-theme-pref", pref);
    if (pref === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", pref);
    }
    try {
      window.localStorage.setItem("prat-theme", pref);
    } catch (e) {
      /* storage unavailable: the choice lasts for this page only */
    }
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      button.setAttribute("aria-label", "Colour theme: " + THEME_NAMES[pref]);
    });
  }

  function cycleTheme() {
    var current = document.documentElement.getAttribute("data-theme-pref") || "system";
    applyTheme(THEMES[(THEMES.indexOf(current) + 1) % THEMES.length]);
  }

  document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    button.addEventListener("click", cycleTheme);
  });
  applyTheme(document.documentElement.getAttribute("data-theme-pref") || "system");

  // Shortcuts dialog.
  function openShortcuts() {
    var dialog = document.getElementById("shortcuts-dialog");
    if (dialog && typeof dialog.showModal === "function" && !dialog.open) {
      dialog.showModal();
    }
  }
  document.querySelectorAll("[data-shortcuts-open]").forEach(function (button) {
    button.addEventListener("click", openShortcuts);
  });

  // Selects that apply on change (the board's project filter).
  document.querySelectorAll("select[data-autosubmit]").forEach(function (select) {
    select.addEventListener("change", function () {
      if (select.form) {
        select.form.submit();
      }
    });
  });

  // Links that move focus to a field (for example "Request rework").
  document.body.addEventListener("click", function (evt) {
    var link = evt.target.closest ? evt.target.closest("[data-focus-target]") : null;
    if (!link) {
      return;
    }
    var target = document.getElementById(link.getAttribute("data-focus-target"));
    if (target) {
      evt.preventDefault();
      target.scrollIntoView({ block: "center", behavior: "smooth" });
      target.focus({ preventScroll: true });
    }
  });

  // Keyboard shortcuts.
  function isTyping(el) {
    if (!el || !el.tagName) {
      return false;
    }
    var tag = el.tagName.toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
  }

  var goPending = 0;
  var GO = { b: "/board", w: "/wall", p: "/projects", i: "/improve", u: "/usage", s: "/settings" };
  document.addEventListener("keydown", function (evt) {
    if (evt.defaultPrevented || evt.ctrlKey || evt.metaKey || evt.altKey) {
      return;
    }
    if (isTyping(evt.target)) {
      if (evt.key === "Escape") {
        evt.target.blur();
      }
      return;
    }
    if (document.querySelector("dialog[open]")) {
      return;
    }
    var key = evt.key;
    if (goPending && Date.now() - goPending < 1500) {
      goPending = 0;
      if (GO[key]) {
        evt.preventDefault();
        window.location.href = GO[key];
      }
      return;
    }
    if (key === "g") {
      goPending = Date.now();
      return;
    }
    if (key === "?") {
      evt.preventDefault();
      openShortcuts();
      return;
    }
    if (key === "t") {
      evt.preventDefault();
      cycleTheme();
      return;
    }
    if (!/^[a-z]$/.test(key)) {
      return;
    }
    var el = document.querySelector('[data-shortcut="' + key + '"]');
    if (!el) {
      return;
    }
    evt.preventDefault();
    if (isTyping(el)) {
      el.scrollIntoView({ block: "center" });
      el.focus();
    } else {
      el.click();
    }
  });

  // Elapsed-time counters for running steps.
  function pad(n) {
    return n < 10 ? "0" + n : String(n);
  }
  function elapsed(seconds) {
    if (seconds < 60) {
      return seconds + "s";
    }
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) {
      return minutes + "m " + pad(seconds % 60) + "s";
    }
    return Math.floor(minutes / 60) + "h " + pad(minutes % 60) + "m";
  }
  function tick() {
    document.querySelectorAll("[data-since]").forEach(function (el) {
      var started = Date.parse(el.getAttribute("data-since"));
      if (!isNaN(started)) {
        el.textContent = elapsed(Math.max(0, Math.floor((Date.now() - started) / 1000)));
      }
    });
  }
  tick();
  window.setInterval(tick, 1000);

  // Board: refresh the columns when anything changes.
  var board = document.querySelector("[data-board-stream]");
  if (board && window.EventSource && htmx) {
    var boardTimer = 0;
    var boardSource = new EventSource(board.getAttribute("data-board-stream"));
    boardSource.addEventListener("board", function () {
      window.clearTimeout(boardTimer);
      boardTimer = window.setTimeout(function () {
        htmx.trigger(board, "prat-refresh");
      }, 250);
    });
    window.addEventListener("pagehide", function () {
      boardSource.close();
    });
  }

  // Card: live session log and panel refreshes.
  var cardSource = null;
  var cardPanel = null;
  var panelTimer = 0;

  function nearBottom(el) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  }

  function closeCardStream() {
    if (cardSource) {
      cardSource.close();
      cardSource = null;
    }
  }

  function refreshPanel() {
    window.clearTimeout(panelTimer);
    panelTimer = window.setTimeout(function () {
      var panel = document.getElementById("card-panel");
      if (panel && htmx) {
        htmx.trigger(panel, "prat-refresh");
      }
    }, 300);
  }

  function appendEntry(html, eventId) {
    var log = document.getElementById("live-log");
    if (!log) {
      return;
    }
    var seq = parseInt(String(eventId || "").split(":")[1] || "0", 10);
    var last = log.lastElementChild;
    var lastSeq = last ? parseInt(last.getAttribute("data-seq") || "0", 10) : 0;
    if (seq && seq <= lastSeq) {
      return;
    }
    var stick = nearBottom(log);
    // Server-rendered through an autoescaping template; see routes_stream.py.
    log.insertAdjacentHTML("beforeend", html);
    var waiting = document.getElementById("live-waiting");
    if (waiting) {
      waiting.remove();
    }
    if (stick) {
      log.scrollTop = log.scrollHeight;
    }
  }

  function syncCardStream() {
    var panel = document.getElementById("card-panel");
    if (panel === cardPanel) {
      return;
    }
    cardPanel = panel;
    closeCardStream();
    if (!panel || panel.getAttribute("data-live") !== "1" || !window.EventSource) {
      return;
    }
    var log = document.getElementById("live-log");
    if (log) {
      log.scrollTop = log.scrollHeight;
    }
    var url = panel.getAttribute("data-stream") + "?after=" + encodeURIComponent(panel.getAttribute("data-after") || "0");
    if (panel.getAttribute("data-run")) {
      url += "&run=" + encodeURIComponent(panel.getAttribute("data-run"));
    }
    var source = new EventSource(url);
    cardSource = source;
    source.addEventListener("entry", function (evt) {
      appendEntry(evt.data, evt.lastEventId);
    });
    source.addEventListener("status", refreshPanel);
    source.addEventListener("run", function () {
      var current = document.getElementById("live-log");
      if (current) {
        current.textContent = "";
      }
      refreshPanel();
    });
    source.addEventListener("end", function () {
      source.close();
      refreshPanel();
    });
  }

  document.body.addEventListener("htmx:afterSettle", syncCardStream);
  window.addEventListener("pagehide", closeCardStream);
  syncCardStream();

  // Copy buttons: data-copy holds a selector for the element whose text to copy.
  document.addEventListener("click", function (evt) {
    var button = evt.target.closest ? evt.target.closest("[data-copy]") : null;
    if (!button) {
      return;
    }
    var source = document.querySelector(button.getAttribute("data-copy"));
    var label = button.querySelector("[data-copy-label]");
    if (!source || !navigator.clipboard) {
      return;
    }
    navigator.clipboard.writeText(source.textContent).then(function () {
      if (label) {
        label.textContent = "Copied";
        setTimeout(function () { label.textContent = "Copy"; }, 1600);
      }
    });
  });
})();
