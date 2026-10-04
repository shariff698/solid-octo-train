const q = document.getElementById('q'), box = document.getElementById('suggest');
let timer;

document.getElementById('burger').onclick = () => document.getElementById('menu').classList.toggle('open');

q.addEventListener('input', () => {
  clearTimeout(timer);
  const v = q.value.trim();
  if (!v) { box.style.display = 'none'; return; }
  timer = setTimeout(async () => {
    const r = await fetch('/api/search/?q=' + encodeURIComponent(v)), d = await r.json();
    box.innerHTML = d.results.map(x =>
      `<a href="${x.url}"><span>${x.title.replace(/</g, '&lt;')}</span><b>KES ${Math.round(x.price)}</b></a>`).join('');
    box.style.display = d.results.length ? 'block' : 'none';
  }, 250);
});
document.addEventListener('click', e => { if (!e.target.closest('.search')) box.style.display = 'none'; });

const ord = document.getElementById('order');
if (ord && ord.dataset.pending) {
  const t = setInterval(async () => {
    const d = await (await fetch(ord.dataset.url)).json();
    if (d.status !== 'pending') { clearInterval(t); location.reload(); }
  }, 3000);
}
