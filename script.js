/* ═══════════════════════════════════════════════════
   VISIONGUARD AI — BRUTALIST TELEMETRY SCRIPTS v2
   ═══════════════════════════════════════════════════ */

(function () {
  'use strict';

  // ─── LIVE RAW TIMESTAMP ───
  const timestampEl = document.getElementById('timestamp');

  function updateTimestamp() {
    const now = new Date();
    const y = now.getFullYear();
    const mo = String(now.getMonth() + 1).padStart(2, '0');
    const d = String(now.getDate()).padStart(2, '0');
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    const ms = String(now.getMilliseconds()).padStart(3, '0').substring(0, 2);

    timestampEl.textContent = `${y}-${mo}-${d} ${h}:${m}:${s}.${ms}`;
    requestAnimationFrame(updateTimestamp);
  }

  requestAnimationFrame(updateTimestamp);

  // ─── MODAL HANDLING ───
  const modal = document.getElementById('form-modal');
  const requestBtn = document.getElementById('request-btn');
  const closeBtn = document.getElementById('close-modal');
  const form = document.getElementById('contact-form');
  const formInner = document.getElementById('form-inner');
  const successMsg = document.getElementById('success-message');

  modal.classList.add('hidden');

  function openModal() {
    form.reset();
    modal.classList.remove('hidden');
    formInner.style.display = 'block';
    successMsg.classList.add('hidden');
  }

  requestBtn.addEventListener('click', openModal);

  closeBtn.addEventListener('click', () => {
    modal.classList.add('hidden');
  });

  // Close on outside click
  modal.addEventListener('click', (e) => {
    if (e.target === modal) {
      modal.classList.add('hidden');
    }
  });

  // Close on ESC key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
      modal.classList.add('hidden');
    }
  });

  // Form submission
  form.addEventListener('submit', (e) => {
    e.preventDefault();

    const name = document.getElementById('name').value;
    const email = document.getElementById('email').value;
    const company = document.getElementById('company').value;

    if (!name || !email || !company) return;

    const submitBtn = form.querySelector('.submit-btn');
    const originalText = submitBtn.textContent;
    submitBtn.textContent = 'AUTHENTICATING...';
    submitBtn.disabled = true;

    // Send form data securely via FormSubmit
    fetch("https://formsubmit.co/ajax/tenusersclub@gmail.com", {
      method: "POST",
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
      },
      body: JSON.stringify({
        name: name,
        company: company,
        email: email,
        _subject: "New VisionGuard AI Demo Request"
      })
    })
      .then(response => response.json())
      .then(data => {
        formInner.style.display = 'none';
        successMsg.classList.remove('hidden');

        setTimeout(() => {
          // Redirect to booking page
          window.location.href = 'https://cal.com/shivambhatia';
        }, 1500);
      })
      .catch(error => {
        console.error('Submission Error:', error);
        submitBtn.textContent = 'ERROR - TRY AGAIN';
        submitBtn.disabled = false;
      });
  });

  // ─── GLITCH EFFECT (Occasional jitter on text) ───
  function triggerGlitch() {
    const elements = document.querySelectorAll('h1, .stat-value, .hero-headline');
    if (elements.length > 0) {
      const idx = Math.floor(Math.random() * elements.length);
      const el = elements[idx];

      const originalTransform = el.style.transform;
      const originalFilter = el.style.filter;

      // Random glitch type
      const glitchType = Math.random();

      if (glitchType < 0.5) {
        // Horizontal jitter
        el.style.transform = `translateX(${Math.random() < 0.5 ? '-' : ''}${1 + Math.random() * 2}px)`;
        el.style.opacity = '0.7';
      } else {
        // Color split / chromatic aberration
        el.style.textShadow = `${Math.random() < 0.5 ? '-' : ''}2px 0 #ff0000, ${Math.random() < 0.5 ? '' : '-'}2px 0 #00ffff`;
        el.style.opacity = '0.85';
      }

      setTimeout(() => {
        el.style.transform = originalTransform;
        el.style.filter = originalFilter;
        el.style.opacity = '1';
        el.style.textShadow = '';
      }, 60 + Math.random() * 40);
    }

    // Randomize next glitch between 3-8 seconds
    setTimeout(triggerGlitch, Math.random() * 5000 + 3000);
  }

  setTimeout(triggerGlitch, 2500);



})();
