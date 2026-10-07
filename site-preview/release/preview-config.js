/* Candidate for a future coordinated release. Not loaded by the local preview. */
(() => {
  const apiBase = 'https://stima360-backend.onrender.com';
  const publicSiteOrigin = 'https://www.stima360.it';
  const detailPages = new Set(['stima_dettagliata.html', 'pdf_redirect.html']);

  function httpUrl(value, base) {
    const url = new URL(value, base);
    if (!['http:', 'https:'].includes(url.protocol)) throw new Error('URL non valido');
    return url;
  }

  function apiUrl(path) {
    const url = httpUrl(path, apiBase);
    if (url.origin !== apiBase || !url.pathname.startsWith('/api/')) {
      throw new Error('Endpoint non valido');
    }
    return url.href;
  }

  function pageUrl(serverUrl, pageName, token) {
    const supplied = httpUrl(serverUrl, publicSiteOrigin);
    if (!detailPages.has(pageName) || supplied.origin !== publicSiteOrigin
        || supplied.pathname !== '/' + pageName
        || typeof token !== 'string' || !token.trim()
        || supplied.searchParams.get('token') !== token) {
      throw new Error('Link di dettaglio non valido');
    }
    const target = new URL('/' + pageName, publicSiteOrigin);
    target.searchParams.set('token', token);
    return target.href;
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
})();
