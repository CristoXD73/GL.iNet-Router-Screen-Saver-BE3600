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

  /* The two pages of the Studio, as a segmented control. `here` is "motion" or "fan". */
  function nav(here) {
    var el = document.createElement("nav");
    el.className = "studio-nav";
    el.setAttribute("aria-label", "Studio");
    el.innerHTML = '<a href="./">Motion</a><a href="fan.html">Fan</a>';
    el.children[here === "fan" ? 1 : 0].className = "on";
    el.children[here === "fan" ? 1 : 0].setAttribute("aria-current", "page");
    return el;
  }

  /* The walkthrough. Shown the first time someone opens the page, and any time after that
   * from the ? button. Each step is { art: svg, h: heading, p: text }. */
  function guide(opts) {
    var seen = "be3600-guide-" + opts.key, back = document.createElement("div"), box, i, s, html;

    back.className = "guide-back hidden";
    html = '<div class="guide" role="dialog" aria-modal="true" aria-label="' + opts.title + '">'
         + '<h2></h2><p></p><div class="guide-steps"></div>'
         + '<div class="guide-foot"><small></small><button class="dark-btn" type="button">Got it</button></div></div>';
    back.innerHTML = html;
    box = back.firstChild;
    box.querySelector("h2").textContent = opts.title;
    box.querySelector("p").textContent = opts.intro || "";
    for (i = 0; i < opts.steps.length; i++) {
      s = opts.steps[i];
      var step = document.createElement("div");
      step.className = "guide-step";
      step.innerHTML = '<figure>' + s.art + '</figure><b><i>' + (i + 1) + '</i><span></span></b><small></small>';
      step.querySelector("b span").textContent = s.h;
      step.querySelector("small").textContent = s.p;
      box.querySelector(".guide-steps").appendChild(step);
    }
    box.querySelector(".guide-foot small").textContent = opts.foot || "";
    document.body.appendChild(back);

    function close() { back.classList.add("hidden"); save(seen, "1"); }
    function open() { back.classList.remove("hidden"); }
    box.querySelector(".guide-foot button").addEventListener("click", close);
    back.addEventListener("click", function (e) { if (e.target === back) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
    if (load(seen, "") !== "1") open();
    return { open: open, close: close };
  }

  w.Studio = { toast: toast, copy: copy, load: load, save: save, mark: mark, nav: nav, guide: guide };
})(window);
