(function () {
  var root = document.documentElement;
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Theme toggle */
  var buttons = document.querySelectorAll(".theme-toggle");
  function label() {
    var dark = root.getAttribute("data-theme") === "dark";
    buttons.forEach(function (b) { b.textContent = dark ? "Light mode" : "Dark mode"; });
  }
  label();
  buttons.forEach(function (b) {
    b.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("theme", next); } catch (e) {}
      label();
    });
  });

  /* Greeting based on the visitor's local time */
  var greeting = document.querySelector("[data-greeting]");
  if (greeting) {
    var h = new Date().getHours();
    greeting.textContent = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  }

  if (reduce || !("IntersectionObserver" in window)) { return; }

  /* Reveal sections as they scroll into view */
  var reveal = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) { e.target.classList.add("in-view"); reveal.unobserve(e.target); }
    });
  }, { threshold: 0.12 });
  document.querySelectorAll(".reveal").forEach(function (el) { reveal.observe(el); });

  /* Count numbers up from zero when they appear */
  function countUp(el) {
    var target = parseInt(el.getAttribute("data-count"), 10);
    if (isNaN(target) || target === 0) { return; }
    var start = null, duration = 1200;
    el.textContent = "0";
    function step(t) {
      if (start === null) { start = t; }
      var p = Math.min((t - start) / duration, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * eased);
      if (p < 1) { requestAnimationFrame(step); }
    }
    requestAnimationFrame(step);
  }
  var counter = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) { countUp(e.target); counter.unobserve(e.target); }
    });
  }, { threshold: 0.3 });
  document.querySelectorAll("[data-count]").forEach(function (el) { counter.observe(el); });

  /* Cards tilt gently toward the pointer (mouse devices only) */
  if (window.matchMedia("(hover: hover)").matches) {
    document.querySelectorAll(".tilt").forEach(function (el) {
      el.addEventListener("pointermove", function (e) {
        var r = el.getBoundingClientRect();
        var px = (e.clientX - r.left) / r.width - 0.5;
        var py = (e.clientY - r.top) / r.height - 0.5;
        el.style.setProperty("--ry", (px * 8).toFixed(2) + "deg");
        el.style.setProperty("--rx", (-py * 8).toFixed(2) + "deg");
      });
      el.addEventListener("pointerleave", function () {
        el.style.setProperty("--rx", "0deg");
        el.style.setProperty("--ry", "0deg");
      });
    });
  }
})();
