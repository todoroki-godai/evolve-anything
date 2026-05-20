(function () {
  const links = document.querySelectorAll('.sidebar-link[data-section]');
  const sections = document.querySelectorAll('.doc-section');

  // ── ページ単位のアクティブ検出 ──
  const page = location.pathname.split('/').pop() || 'index.html';
  links.forEach(l => {
    const href = l.getAttribute('href') || '';
    if (href.startsWith(page) || (page === 'index.html' && href.startsWith('#'))) {
      l.classList.add('current-page');
    }
  });

  // ── IntersectionObserver でセクションをトラック ──
  function setActive(id) {
    links.forEach(l => {
      const href = l.getAttribute('href') || '';
      const match = href.endsWith('#' + id) || href === '#' + id;
      l.classList.toggle('active', match);
    });
  }

  if (sections.length > 0) {
    setActive(sections[0].id);

    const observer = new IntersectionObserver(entries => {
      const visible = entries
        .filter(e => e.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible.length > 0) setActive(visible[0].target.id);
    }, { rootMargin: '-60px 0px -60% 0px', threshold: 0 });

    sections.forEach(s => observer.observe(s));

    let t;
    window.addEventListener('scroll', () => {
      clearTimeout(t);
      t = setTimeout(() => {
        const y = window.scrollY + 100;
        let cur = sections[0].id;
        sections.forEach(s => { if (s.offsetTop <= y) cur = s.id; });
        setActive(cur);
      }, 50);
    }, { passive: true });
  }

  // ── スクロールトップボタン ──
  const btn = document.getElementById('scrollTop');
  if (btn) {
    window.addEventListener('scroll', () => {
      btn.classList.toggle('visible', window.scrollY > 400);
    }, { passive: true });
  }

  // ── モバイルサイドバートグル ──
  const toggle = document.getElementById('sidebarToggle');
  const sidebar = document.getElementById('sidebar');
  if (toggle && sidebar) {
    toggle.addEventListener('click', () => sidebar.classList.toggle('open'));
    document.querySelectorAll('.sidebar-link').forEach(l => {
      l.addEventListener('click', () => {
        if (window.innerWidth <= 768) sidebar.classList.remove('open');
      });
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') sidebar.classList.remove('open');
    });
  }

  // ── スムーススクロール（同一ページ内 # リンクのみ） ──
  document.querySelectorAll('a[href^="#"]').forEach(a => {
    a.addEventListener('click', e => {
      const target = document.querySelector(a.getAttribute('href'));
      if (!target) return;
      e.preventDefault();
      window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - 72, behavior: 'smooth' });
    });
  });
})();
