document.addEventListener('DOMContentLoaded', () => {
    const menuButton = document.querySelector('.hamburger');
    const nav = document.querySelector('.nav-list');
    const navbar = document.getElementById('navbar');
    function closeMenu() {
        menuButton?.classList.remove('active');
        nav?.classList.remove('active');
        menuButton?.setAttribute('aria-expanded', 'false');
        menuButton?.setAttribute('aria-label', 'Apri il menu');
    }
    menuButton?.addEventListener('click', () => {
        const expanded = menuButton.getAttribute('aria-expanded') !== 'true';
        menuButton.classList.toggle('active', expanded);
        nav?.classList.toggle('active', expanded);
        menuButton.setAttribute('aria-expanded', String(expanded));
        menuButton.setAttribute('aria-label', expanded ? 'Chiudi il menu' : 'Apri il menu');
    });
    document.querySelectorAll('.nav-link').forEach(link => link.addEventListener('click', closeMenu));
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && menuButton?.getAttribute('aria-expanded') === 'true') {
            closeMenu();
            menuButton.focus();
        }
    });
    document.addEventListener('click', event => {
        if (navbar && !navbar.contains(event.target)) closeMenu();
    });
    window.matchMedia('(min-width: 901px)').addEventListener('change', event => { if (event.matches) closeMenu(); });
    function updateNavbar() { navbar?.classList.toggle('scrolled', window.scrollY > 30); }
    window.addEventListener('scroll', updateNavbar, { passive: true });
    updateNavbar();
    document.querySelectorAll('[data-open-chat]').forEach(button => {
        button.addEventListener('click', () => {
            if (document.getElementById('shop-chat')?.hidden) document.getElementById('chat-toggle')?.click();
            else document.getElementById('chat-close')?.focus();
        });
    });
    const faders = document.querySelectorAll('.fade-in-scroll');
    if ('IntersectionObserver' in window && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        document.documentElement.classList.add('has-motion');
        const observer = new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;
                entry.target.classList.add('visible');
                observer.unobserve(entry.target);
            });
        }, { threshold: 0.08 });
        faders.forEach(item => observer.observe(item));
    }
});
