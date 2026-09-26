// Applies the saved (or system) colour theme before the first paint, so the
// page never flashes the wrong theme. A file rather than an inline script,
// because the Content-Security-Policy only allows same-origin scripts.
(function () {
  var theme = "light";
  try {
    var saved = localStorage.getItem("theme");
    theme = saved === "dark" || saved === "light" ? saved : window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch (e) {}
  document.documentElement.dataset.theme = theme;
})();
