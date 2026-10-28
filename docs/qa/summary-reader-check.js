
(() => {
  const links = [...document.querySelectorAll('.toc a')];
  const chapters = [...document.querySelectorAll('.chapter')];
  const toc = document.querySelector('.toc-panel');
  if (matchMedia('(max-width:920px)').matches) toc.open = false;
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting).sort((a,b) => a.boundingClientRect.top-b.boundingClientRect.top);
      if (!visible.length) return;
      const id = visible[0].target.id;
      links.forEach(link => link.getAttribute('href') === '#' + id ? link.setAttribute('aria-current','location') : link.removeAttribute('aria-current'));
    }, {rootMargin:'-5% 0px -65% 0px',threshold:0});
    chapters.forEach(chapter => observer.observe(chapter));
  }
  links.forEach(link => link.addEventListener('click', () => {
    if (matchMedia('(max-width:920px)').matches) toc.open = false;
  }));
  const dialog = document.querySelector('#image-dialog');
  if (!dialog || typeof dialog.showModal !== 'function') return;
  const image = dialog.querySelector('img');
  const title = dialog.querySelector('h2');
  const status = dialog.querySelector('.image-status');
  let origin = null, pending = null;
  function clearWait() { clearTimeout(pending); pending = null; }
  image.onload = () => { clearWait(); image.hidden=false; status.hidden=true; };
  image.onerror = () => { clearWait(); image.hidden=true; status.hidden=false; status.textContent='图片未能打开，请关闭此窗口，在正文中查看。'; };
  document.querySelectorAll('.figure-action').forEach(button => {
    button.hidden = false;
    button.addEventListener('click', () => {
      origin=button; clearWait();
      const source = button.closest('figure').querySelector('img');
      title.textContent=source.alt; image.alt=source.alt;
      status.textContent='正在打开图片…'; status.hidden=false; image.hidden=true;
      image.src=source.src; dialog.showModal();
      if (image.complete && image.naturalWidth) { image.hidden=false; status.hidden=true; }
      else pending=setTimeout(() => { status.textContent='图片打开较慢，可关闭此窗口后在正文中查看。'; },15000);
    });
  });
  dialog.querySelector('.close-image').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => { clearWait(); image.removeAttribute('src'); if (origin) origin.focus(); });
})();
