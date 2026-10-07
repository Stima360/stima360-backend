/* Local review configuration. This file is deliberately unsuitable for public deployment. */
(() => {
  const apiBase = window.STIMA360_PREVIEW_API_BASE || 'http://127.0.0.1:8000';
  const loopbackHosts = new Set(['localhost', '127.0.0.1', '[::1]']);

  function localHttpUrl(value) {
    const url = new URL(value, location.href);
    if (!['http:', 'https:'].includes(url.protocol) || !loopbackHosts.has(url.hostname)) {
      throw new Error('La copia di verifica usa soltanto servizi locali');
    }
    return url;
  }

  function apiUrl(path) {
    const base = localHttpUrl(apiBase);
    const url = new URL(path, base);
    if (url.origin !== base.origin || !url.pathname.startsWith('/api/')) {
      throw new Error('Endpoint non valido');
    }
    return url.href;
  }

  function pageUrl(serverUrl, pageName, token) {
    const supplied = new URL(serverUrl, location.href);
    if (!['http:', 'https:'].includes(supplied.protocol)
        || supplied.pathname.split('/').pop() !== pageName
        || !token || supplied.searchParams.get('token') !== token) {
      throw new Error('Link di dettaglio non valido');
    }
    const local = new URL(pageName, location.href);
    local.search = '';
    local.searchParams.set('token', token);
    return local.href;
  }

  function pdfUrl(stimaId, token, retry = false) {
    if (!Number.isSafeInteger(stimaId) || stimaId <= 0
        || typeof token !== 'string' || !token.trim() || typeof retry !== 'boolean') {
      throw new Error('Link PDF non valido');
    }
    return apiUrl('/api/stime/' + stimaId + '/pdf' + (retry ? '/retry' : '')
      + '?t=' + encodeURIComponent(token));
  }

  window.Stima360Preview = Object.freeze({ apiUrl, pageUrl, pdfUrl });
  // The copied preview does not send analytics events.
  window.fbq = () => {};
})();
