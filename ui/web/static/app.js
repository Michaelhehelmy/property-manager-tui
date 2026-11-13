(function () {
  function typewrite(el) {
    if (!el || el.dataset.typed === '1') return;
    const full = el.getAttribute('data-fulltext');
    if (full == null) return;
    el.dataset.typed = '1';
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || full.length === 0) {
      el.textContent = full;
      el.classList.remove('typing');
      return;
    }
    let i = 0;
    const speed = full.length > 600 ? 2 : (full.length > 200 ? 4 : 12);
    const step = full.length > 600 ? 4 : (full.length > 200 ? 2 : 1);
    el.textContent = '';
    el.classList.add('typing');
    function tick() {
      if (!el.isConnected) return;
      i = Math.min(full.length, i + step);
      el.textContent = full.slice(0, i);
      const container = document.getElementById('assistant-messages');
      if (container) container.scrollTop = container.scrollHeight;
      if (i < full.length) {
        setTimeout(tick, speed);
      } else {
        el.classList.remove('typing');
      }
    }
    tick();
  }

  function runTypewriter(root) {
    const scope = root || document;
    const nodes = scope.querySelectorAll ? scope.querySelectorAll('[data-animate="1"] .assistant-content[data-fulltext]') : [];
    nodes.forEach(typewrite);
    // also handle if root itself is container
    if (scope.querySelectorAll) {
      scope.querySelectorAll('.assistant-content[data-animate-self="1"][data-fulltext]').forEach(typewrite);
    }
  }

  function setActiveNav() {
    const path = window.location.pathname.replace(/\/$/, '') || '/';
    document.querySelectorAll('nav a.nav-link').forEach(a => {
      const href = (a.getAttribute('href') || '').replace(/\/$/, '') || '/';
      const active = href === '/' ? path === '/' : (path === href || path.startsWith(href + '/'));
      a.classList.toggle('active', active);
      if (active) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
  }

  function markLoading(on) {
    document.body.classList.toggle('htmx-loading', !!on);
    const bar = document.getElementById('top-progress');
    if (bar) bar.classList.toggle('active', !!on);
  }

  document.addEventListener('DOMContentLoaded', function () {
    setActiveNav();
    runTypewriter(document);
  });

  document.body.addEventListener('htmx:beforeRequest', function () { markLoading(true); });
  document.body.addEventListener('htmx:afterSwap', function (e) {
    markLoading(false);
    runTypewriter(e.target);
  });
  document.body.addEventListener('htmx:afterRequest', function () { markLoading(false); });
  document.body.addEventListener('htmx:responseError', function () { markLoading(false); });
  window.addEventListener('pageshow', setActiveNav);
})();
