// The knowledge graph page: draws entries and files with Cytoscape, shows an entry's details,
// filters and searches, and redraws when the knowledge or the code changes (it checks a small
// version endpoint every few seconds and fetches the graph only when that changes).
(function () {
  "use strict";

  var POLL_MS = 8000;
  var KIND_VAR = {
    convention: "--link",
    pitfall: "--bad",
    command: "--ok",
    decision: "--accent",
    layout: "--neutral",
    stack: "--warn",
  };

  function cssVar(name, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function palette() {
    var kinds = {};
    Object.keys(KIND_VAR).forEach(function (kind) {
      kinds[kind] = cssVar(KIND_VAR[kind], "#3a4868");
    });
    return {
      kinds: kinds,
      text: cssVar("--text", "#101930"),
      faint: cssVar("--text-faint", "#55617d"),
      surface: cssVar("--surface", "#ffffff"),
      surface2: cssVar("--surface-2", "#eef2f8"),
      border: cssVar("--border-strong", "#c3ccdc"),
      link: cssVar("--link", "#1d4ed8"),
      bad: cssVar("--bad", "#b42826"),
      accent: cssVar("--accent", "#f6b73c"),
    };
  }

  function styles() {
    var p = palette();
    var s = [
      {
        selector: "node",
        style: {
          label: "data(short)",
          "font-size": 10,
          color: p.text,
          "text-valign": "bottom",
          "text-margin-y": 4,
          "text-wrap": "ellipsis",
          "text-max-width": 150,
          "text-background-color": p.surface,
          "text-background-opacity": 0.85,
          "text-background-padding": 2,
          "min-zoomed-font-size": 4,
        },
      },
      {
        selector: 'node[type = "entry"]',
        style: {
          shape: "ellipse",
          width: "data(size)",
          height: "data(size)",
          "border-width": 2,
          "border-color": p.surface,
        },
      },
      {
        selector: 'node[type = "entry"][confidence = "confirmed"]',
        style: { "border-color": p.text, "border-width": 2.5 },
      },
      {
        selector: 'node[type = "entry"][status != "active"]',
        style: { opacity: 0.45, "border-style": "dashed", "border-color": p.faint },
      },
      {
        selector: 'node[type = "file"]',
        style: {
          shape: "round-rectangle",
          width: "data(size)",
          height: 12,
          "background-color": p.surface2,
          "border-width": 1,
          "border-color": p.border,
          color: p.faint,
          "font-family": "ui-monospace, Menlo, Consolas, monospace",
          "font-size": 9,
          height: 18,
          "text-valign": "center",
          "text-margin-y": 0,
          "text-max-width": 200,
          "text-background-opacity": 0,
        },
      },
      {
        selector: 'node[type = "project"]',
        style: {
          shape: "round-rectangle",
          width: "data(size)",
          height: 26,
          "background-color": p.text,
          color: p.surface,
          "font-size": 11,
          "font-weight": 600,
          "text-valign": "center",
          "text-margin-y": 0,
          "text-background-opacity": 0,
        },
      },
      {
        selector: 'node[type = "file"][?unknown]',
        style: { "border-style": "dashed", "border-color": p.accent, "border-width": 1.5 },
      },
      {
        selector: "edge",
        style: {
          width: 1.4,
          "curve-style": "bezier",
          "line-color": p.border,
          "target-arrow-color": p.border,
          "arrow-scale": 0.7,
          opacity: 0.85,
        },
      },
      { selector: 'edge[relation = "about"]', style: { "line-color": p.link, opacity: 0.5 } },
      {
        selector: 'edge[relation = "related"], edge[relation = "refines"]',
        style: { "line-color": p.link, width: 2, "target-arrow-shape": "triangle", "target-arrow-color": p.link },
      },
      {
        selector: 'edge[relation = "contradicts"]',
        style: { "line-color": p.bad, width: 2.2, "line-style": "dashed" },
      },
      {
        selector: 'edge[relation = "superseded_by"]',
        style: { "line-color": p.faint, "line-style": "dotted", "target-arrow-shape": "triangle", "target-arrow-color": p.faint },
      },
      { selector: 'edge[relation = "imports"]', style: { width: 0.8, opacity: 0.45, "target-arrow-shape": "vee" } },
      { selector: 'edge[relation = "in"]', style: { width: 0.6, opacity: 0.18, "line-color": p.border } },
      {
        selector: 'edge[relation = "calls"]',
        style: { width: 0.9, opacity: 0.5, "line-style": "dotted", "line-color": p.link, "target-arrow-shape": "vee", "target-arrow-color": p.link },
      },
      {
        selector: 'edge[relation = "changes_with"]',
        style: { width: 1.2, opacity: 0.6, "line-style": "dashed", "line-color": p.accent },
      },
      {
        selector: 'node[type = "module"]',
        style: {
          shape: "round-rectangle",
          width: "data(size)",
          height: 22,
          "background-color": p.surface2,
          "border-width": 1.5,
          "border-color": p.faint,
          color: p.text,
          "font-size": 10,
          "font-weight": 600,
          "text-valign": "center",
          "text-margin-y": 0,
          "text-max-width": 260,
          "text-background-opacity": 0,
        },
      },
      { selector: 'node[type = "module"][?data]', style: { "border-style": "dashed" } },
      { selector: ".faded", style: { opacity: 0.12 } },
      { selector: "node.match", style: { "underlay-color": p.accent, "underlay-opacity": 0.45, "underlay-padding": 6 } },
      { selector: "node:selected", style: { "underlay-color": p.link, "underlay-opacity": 0.35, "underlay-padding": 7 } },
    ];
    Object.keys(p.kinds).forEach(function (kind) {
      s.push({ selector: 'node[kind = "' + kind + '"]', style: { "background-color": p.kinds[kind] } });
    });
    return s;
  }

  function basename(path) {
    var parts = path.replace(/\/$/, "").split("/");
    return parts[parts.length - 1] || path;
  }

  function elements(data) {
    var out = [];
    data.nodes.forEach(function (n) {
      var d = { id: n.id, type: n.type, label: n.label };
      if (n.type === "entry") {
        d.kind = n.kind;
        d.status = n.status;
        d.confidence = n.confidence;
        d.size = 18 + 4 * Math.min(Math.max(n.helped || 0, 0), 6);
        d.short = n.label.length > 48 ? n.label.slice(0, 46) + "..." : n.label;
      } else if (n.type === "project") {
        d.short = n.label;
        d.size = Math.max(70, 8 * n.label.length + 20);
      } else if (n.type === "module") {
        d.short = n.label + (n.data ? " (" + n.files + " data files)" : " (" + n.files + ")");
        d.size = Math.max(60, Math.min(260, 6.5 * d.short.length + 16));
        d.data = !!n.data;
      } else {
        d.short = basename(n.label);
        // With a deep scan, files grow with how often they changed.
        var churn = Math.min(n.commits || 0, 40);
        d.size = Math.max(40, Math.min(200, 6.2 * d.short.length + 14)) + churn * 2;
        d.unknown = n.commits === undefined ? !n.known : !n.known && churn >= 5;
      }
      out.push({ group: "nodes", data: d });
    });
    data.edges.forEach(function (e, i) {
      out.push({ group: "edges", data: { id: "e" + i, source: e.source, target: e.target, relation: e.relation, count: e.count || 1 } });
    });
    return out;
  }

  function init() {
    var box = document.getElementById("kgraph");
    var status = document.querySelector("[data-kg-status]");
    var detail = document.getElementById("kg-detail");
    if (!box) return;
    function say(text) {
      if (status) status.textContent = text;
    }
    if (typeof window.cytoscape !== "function") {
      say("The graph could not load");
      return;
    }
    var cy = window.cytoscape({
      container: box,
      style: styles(),
      wheelSensitivity: 0.25,
      minZoom: 0.15,
      maxZoom: 3,
      boxSelectionEnabled: false,
    });
    var version = null;
    var loading = false;
    var inactiveBox = document.querySelector("[data-kg-inactive]");

    function applyFilters() {
      var hidden = {};
      document.querySelectorAll("[data-kg-kind]").forEach(function (input) {
        if (!input.checked) hidden[input.getAttribute("data-kg-kind")] = true;
      });
      var files = document.querySelector("[data-kg-files]");
      var showFiles = !files || files.checked;
      var mods = document.querySelector("[data-kg-modules]");
      var showModules = !mods || mods.checked;
      cy.batch(function () {
        cy.nodes().forEach(function (node) {
          var type = node.data("type");
          var visible =
            type === "file" ? showFiles : type === "module" ? showModules : type === "project" ? true : !hidden[node.data("kind")];
          node.style("display", visible ? "element" : "none");
        });
      });
    }

    function search(text) {
      var query = (text || "").trim().toLowerCase();
      cy.batch(function () {
        cy.elements().removeClass("faded match");
        if (!query) return;
        var hits = cy.nodes().filter(function (n) {
          return String(n.data("label")).toLowerCase().indexOf(query) >= 0;
        });
        cy.elements().addClass("faded");
        hits.removeClass("faded").addClass("match");
        hits.connectedEdges().removeClass("faded");
        hits.neighborhood("node").removeClass("faded");
      });
    }

    function showEntry(id) {
      var base = box.getAttribute("data-entry");
      if (!detail || !base) return;
      fetch(base + encodeURIComponent(id), { credentials: "same-origin", headers: { "HX-Request": "true" } })
        .then(function (r) {
          return r.ok ? r.text() : Promise.reject(r.status);
        })
        .then(function (html) {
          detail.innerHTML = html;
          if (window.htmx) window.htmx.process(detail);
        })
        .catch(function () {
          detail.textContent = "Could not load this entry.";
        });
    }

    function select(id) {
      var node = cy.getElementById(id);
      if (node && node.length) {
        cy.nodes().unselect();
        node.select();
        cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 1) }, { duration: 250 });
      }
      showEntry(id);
    }

    function draw(data) {
      var positions = {};
      cy.nodes().forEach(function (n) {
        positions[n.id()] = n.position();
      });
      var fresh = Object.keys(positions).length === 0;
      cy.batch(function () {
        cy.elements().remove();
        cy.add(elements(data));
        cy.nodes().forEach(function (n) {
          if (positions[n.id()]) n.position(positions[n.id()]);
        });
      });
      applyFilters();
      var newcomers = cy.nodes().filter(function (n) {
        return !positions[n.id()];
      });
      if (fresh || newcomers.length) {
        var layout = cy.layout({
          name: "cose",
          animate: !fresh,
          animationDuration: 400,
          randomize: fresh,
          nodeDimensionsIncludeLabels: false,
          nodeRepulsion: function (node) {
            return node.data("type") === "entry" ? 60000 : 20000;
          },
          idealEdgeLength: function (edge) {
            return edge.data("relation") === "imports" ? 70 : 90;
          },
          edgeElasticity: function () {
            return 80;
          },
          nodeOverlap: 40,
          componentSpacing: 60,
          gravity: 0.6,
          numIter: cy.nodes().length > 400 ? 1200 : 2500,
          padding: 24,
          fit: fresh,
        });
        if (fresh) {
          // A small graph should not be blown up past a readable size.
          layout.one("layoutstop", function () {
            if (cy.zoom() > 1.3) {
              cy.zoom(1.3);
              cy.center();
            }
          });
        }
        layout.run();
      }
      var counts = data.counts || {};
      box.setAttribute(
        "aria-label",
        "Graph of " + (counts.entries || 0) + " knowledge entries and " + (counts.files || 0) + " files"
      );
      var finder = document.querySelector("[data-kg-search]");
      if (finder && finder.value) search(finder.value);
    }

    function load() {
      if (loading) return;
      loading = true;
      var url = box.getAttribute("data-src") + (inactiveBox && inactiveBox.checked ? "?inactive=1" : "");
      fetch(url, { credentials: "same-origin" })
        .then(function (r) {
          return r.ok ? r.json() : Promise.reject(r.status);
        })
        .then(function (data) {
          var first = version === null;
          version = data.version;
          draw(data);
          var when = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
          say(first ? "Live" : "Updated at " + when);
          if (!data.nodes.length) say("Nothing learned yet");
        })
        .catch(function () {
          say("Could not load the graph");
        })
        .finally(function () {
          loading = false;
        });
    }

    function poll() {
      if (document.visibilityState !== "visible" || loading) return;
      fetch(box.getAttribute("data-version"), { credentials: "same-origin" })
        .then(function (r) {
          return r.ok ? r.json() : null;
        })
        .then(function (data) {
          // Both endpoints report "<entries hash>:<default branch commit>".
          if (data && version !== null && data.version !== version) {
            load();
          }
        })
        .catch(function () {});
    }

    cy.on("tap", "node", function (evt) {
      var node = evt.target;
      if (node.data("type") === "entry") {
        showEntry(node.id());
      } else if (detail) {
        var unknown = node.data("unknown");
        detail.innerHTML = "";
        var h = document.createElement("h3");
        h.className = "kg-entry-title mono";
        h.textContent = node.data("label");
        var p = document.createElement("p");
        p.className = "muted";
        var about = node.connectedEdges('[relation = "about"]').length;
        var what = node.data("type") === "module" ? "module" : "file";
        p.textContent = unknown
          ? "This file changed often, but nothing is known about it yet."
          : about + (about === 1 ? " entry is" : " entries are") + " about this " + what + ".";
        detail.appendChild(h);
        detail.appendChild(p);
        // The entries about it, to open from here.
        var list = document.createElement("ul");
        list.className = "plain-list kg-history";
        node.connectedEdges('[relation = "about"]').sources().forEach(function (entry) {
          var li = document.createElement("li");
          var a = document.createElement("a");
          a.href = "#kgraph";
          a.setAttribute("data-kg-select", entry.id());
          a.textContent = entry.data("label");
          li.appendChild(a);
          list.appendChild(li);
        });
        if (list.children.length) detail.appendChild(list);
      }
    });

    document.addEventListener("click", function (evt) {
      var link = evt.target.closest ? evt.target.closest("[data-kg-select]") : null;
      if (!link) return;
      evt.preventDefault();
      box.scrollIntoView({ behavior: "smooth", block: "center" });
      select(link.getAttribute("data-kg-select"));
    });
    document.querySelectorAll("[data-kg-kind], [data-kg-files], [data-kg-modules]").forEach(function (input) {
      input.addEventListener("change", applyFilters);
    });
    if (inactiveBox) inactiveBox.addEventListener("change", load);
    var searchBox = document.querySelector("[data-kg-search]");
    if (searchBox) {
      searchBox.addEventListener("input", function () {
        search(searchBox.value);
      });
    }
    var fit = document.querySelector("[data-kg-fit]");
    if (fit) {
      fit.addEventListener("click", function () {
        cy.animate({ fit: { eles: cy.elements(":visible"), padding: 24 } }, { duration: 250 });
      });
    }
    new MutationObserver(function () {
      cy.style(styles());
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    if (window.matchMedia) {
      var dark = window.matchMedia("(prefers-color-scheme: dark)");
      if (dark.addEventListener) {
        dark.addEventListener("change", function () {
          cy.style(styles());
        });
      }
    }
    load();
    setInterval(poll, POLL_MS);
    document.addEventListener("visibilitychange", poll);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
