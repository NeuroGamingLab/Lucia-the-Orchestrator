// === Terminal Typing Animation ===
(function () {
  const commandEl = document.getElementById('typed-command');
  const cursorEl = document.getElementById('cursor');
  const outputEl = document.getElementById('terminal-output');

  const command = 'dave-it-guy deploy openclaw';
  const outputLines = [
    { text: '⠋ Pulling stack manifest...', cls: 'dim' },
    { text: '✓ Stack: openclaw (v1.2.0)', cls: 'success' },
    { text: '⠋ Starting containers...', cls: 'dim' },
    { text: '  → ollama        ✓ running (GPU detected)', cls: 'accent' },
    { text: '  → qdrant        ✓ running', cls: 'accent' },
    { text: '  → openclaw      ✓ running', cls: 'accent' },
    { text: '  → web-ui        ✓ running', cls: 'accent' },
    { text: '', cls: '' },
    { text: '🐙 Stack deployed! Access at http://localhost:3000', cls: 'success' },
  ];

  let charIndex = 0;
  let lineIndex = 0;

  function typeCommand() {
    if (charIndex < command.length) {
      commandEl.textContent += command[charIndex];
      charIndex++;
      setTimeout(typeCommand, 50 + Math.random() * 40);
    } else {
      cursorEl.style.display = 'none';
      setTimeout(printOutput, 400);
    }
  }

  function printOutput() {
    if (lineIndex < outputLines.length) {
      const line = outputLines[lineIndex];
      const div = document.createElement('div');
      if (line.cls) div.className = line.cls;
      div.textContent = line.text || '\u00A0';
      outputEl.appendChild(div);
      lineIndex++;

      const delay = line.cls === 'dim' ? 600 : line.cls === 'accent' ? 300 : 200;
      setTimeout(printOutput, delay);
    }
  }

  // Start after a short delay
  setTimeout(typeCommand, 800);
})();

// === Copy Buttons ===
document.querySelectorAll('.copy-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const text = btn.getAttribute('data-copy');
    navigator.clipboard.writeText(text).then(() => {
      btn.classList.add('copied');
      btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>';
      setTimeout(() => {
        btn.classList.remove('copied');
        btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>';
      }, 2000);
    });
  });
});

// === Scroll-triggered fade-in ===
const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('fade-in');
        observer.unobserve(entry.target);
      }
    });
  },
  { threshold: 0.1, rootMargin: '0px 0px -40px 0px' }
);

document.querySelectorAll('.feature-card, .stack-card, .pricing-card, .install-step').forEach(el => {
  observer.observe(el);
});

// === Smooth scroll for anchor links (fallback) ===
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
  anchor.addEventListener('click', (e) => {
    const target = document.querySelector(anchor.getAttribute('href'));
    if (target) {
      e.preventDefault();
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });
});
