// Applies the saved colour theme before the page paints. Loaded synchronously
// from <head>; the Content Security Policy forbids inline scripts.
(function () {
  "use strict";
  var pref = "system";
  try {
    pref = window.localStorage.getItem("prat-theme") || "system";
  } catch (e) {
    pref = "system";
  }
  if (pref !== "light" && pref !== "dark") {
    pref = "system";
  }
  var root = document.documentElement;
  root.setAttribute("data-theme-pref", pref);
  if (pref === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", pref);
  }
})();
