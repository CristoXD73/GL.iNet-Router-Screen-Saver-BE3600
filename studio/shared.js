/* Shared by Motion Studio and Fan Studio. No dependencies, no build step. */
(function (w) {
  "use strict";

  var timer = null;

  /* Shows a message at the bottom of the page. Creates the element if the page has none. */
  function toast(msg, ms) {
    var el = document.querySelector(".toast");

    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(timer);
    timer = setTimeout(function () { el.classList.remove("show"); }, ms || 1700);
  }

  /* Copies text, and says so. Falls back to telling the reader where to find it. */
  function copy(text, whereItIs) {
    if (w.navigator && w.navigator.clipboard) {
      w.navigator.clipboard.writeText(text).then(
        function () { toast("copied"); },
        function () { toast(whereItIs || "could not copy"); }
      );
    } else {
      toast(whereItIs || "could not copy");
    }
  }

  /* localStorage that cannot throw: private windows and blocked site data return the fallback. */
  function load(key, fallback) {
    try {
      var v = w.localStorage.getItem(key);
      return v === null ? fallback : v;
    } catch (e) { return fallback; }
  }

  function save(key, value) {
    try { w.localStorage.setItem(key, value); return true; } catch (e) { return false; }
  }

  /* The logo markup, so neither page has to spell it out. */
  function mark() {
    return '<div class="mark" aria-hidden="true"><div class="pair"><i class="bot-eye"></i><i class="bot-eye"></i></div></div>';
  }

  w.Studio = { toast: toast, copy: copy, load: load, save: save, mark: mark };
})(window);
