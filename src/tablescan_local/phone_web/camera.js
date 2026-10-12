'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let copy = {}, token = '', connected = false, sending = false, checking = false;
  const photos = [];
  const text = key => copy[key] || key;
  const notice = key => { $('notice').textContent = text(key); $('notice').hidden = false; };
  const setConnected = value => {
    connected = value;
    $('connection').textContent = text(value ? 'connected' : 'disconnected');
    $('connection').classList.toggle('online', value);
    $('camera').disabled = !value;
    $('camera-button').classList.toggle('disabled', !value);
    $('reconnect').hidden = value;
  };
  async function request(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 125000);
    try {
      return await fetch(path, {...options, cache: 'no-store', credentials: 'omit',
        headers: {...options.headers, Authorization: `Bearer ${token}`}, signal: controller.signal});
    } finally { clearTimeout(timeout); }
  }
  async function checkConnection() {
    if (checking) return;
    checking = true;
    try {
      if (!/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error('invalid_link');
      const response = await request('/api/status');
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || 'disconnected');
      }
      setConnected(true);
      $('notice').hidden = true;
    } catch (error) {
      setConnected(false);
      notice(['invalid_link', 'session_ended'].includes(error.message) ? error.message : 'connection_help');
    } finally { checking = false; }
  }
  function redraw(photo) {
    photo.row.className = photo.state;
    photo.status.textContent = text(photo.error || photo.state);
    photo.retry.hidden = photo.state !== 'failed';
    $('count').textContent = `${photos.filter(item => item.state === 'saved').length} / ${photos.length}`;
  }
  async function sendQueue() {
    if (sending) return;
    sending = true;
    try {
      for (const photo of photos) {
        if (photo.state !== 'queued') continue;
        photo.state = 'sending'; photo.error = ''; redraw(photo);
        try {
          const response = await request(`/api/photos/${photo.id}`, {
            method: 'PUT', body: photo.file, headers: {'Content-Type': 'application/octet-stream'}
          });
          const data = await response.json();
          if (!response.ok || !data.saved) throw new Error(data.error || 'save_failed');
          photo.state = 'saved'; photo.file = null;
          setConnected(true);
        } catch (error) {
          photo.state = 'failed';
          photo.error = Object.hasOwn(copy, error.message) ? error.message : 'retry';
          if (['invalid_link', 'session_ended'].includes(error.message)) {
            setConnected(false); notice(error.message);
          }
        }
        redraw(photo);
      }
    } finally {
      sending = false;
      if (photos.some(photo => photo.state === 'queued')) sendQueue();
    }
  }
  $('camera').addEventListener('change', event => {
    const file = event.target.files && event.target.files[0];
    event.target.value = '';
    if (!file) return;
    if (file.size > 40 * 1024 * 1024 || !file.size) { notice('photo_too_large'); return; }
    if (photos.filter(photo => photo.file).length >= 20) { notice('queue_full'); return; }
    // getRandomValues works on LAN HTTP; randomUUID requires a secure context.
    const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
    const row = document.createElement('li');
    const number = document.createElement('span'); number.className = 'photo-number'; number.textContent = String(photos.length + 1);
    const content = document.createElement('div'); content.className = 'photo-text';
    const name = document.createElement('div'); name.className = 'photo-name'; name.textContent = file.name || text('photo');
    const status = document.createElement('div'); status.className = 'photo-status';
    const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'retry'; retry.textContent = text('retry_button');
    const photo = {id, file, row, status, retry, state: 'queued', error: ''};
    retry.addEventListener('click', () => { photo.state = 'queued'; photo.error = ''; redraw(photo); sendQueue(); });
    content.append(name, status); row.append(number, content, retry); $('receipts').prepend(row);
    photos.push(photo); $('empty').hidden = true; $('notice').hidden = true;
    redraw(photo); sendQueue();
  });
  $('reconnect').addEventListener('click', checkConnection);
  window.addEventListener('online', checkConnection);
  window.addEventListener('beforeunload', event => {
    if (photos.some(photo => photo.file)) { event.preventDefault(); event.returnValue = ''; }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkConnection(); });
  async function init() {
    try {
      const response = await fetch('/copy.json', {cache: 'no-store'});
      if (!response.ok) throw new Error('Cannot load the phone page');
      copy = await response.json();
      document.documentElement.lang = copy.language || 'en';
      document.title = `TableScan · ${text('phone')}`;
      for (const node of document.querySelectorAll('[data-copy]')) node.textContent = text(node.dataset.copy);
      $('camera').setAttribute('aria-label', text('take_photo'));
      token = location.hash.slice(1);
      try {
        if (token) sessionStorage.setItem('tablescan-token', token);
        else token = sessionStorage.getItem('tablescan-token') || '';
      } catch (_) { /* Private browsing may disable storage. */ }
      history.replaceState(null, '', location.pathname);
      await checkConnection();
      setInterval(() => { if (!document.hidden && !sending) checkConnection(); }, 10000);
    } catch (_) {
      $('connection').textContent = 'Connection unavailable';
      $('notice').textContent = 'Open a new connection in TableScan and scan its QR code again.';
      $('notice').hidden = false;
    }
  }
  init();
})();
