// STIMA360 OS — territorio/maplibre-loader.js (STIMA360 TERRITORIO T2)
//
// Carica MapLibre GL JS SOLO quando serve (la prima apertura di
// `#/edifici/mappa`), dal CDN gratuito unpkg, a una versione FISSA e con
// l'impronta SRI del file pubblicato su npm: un file diverso da quello atteso
// non viene eseguito. Nessuna chiave, nessun account, nessun costo.
//
// Perche' la 5.24.0 e non la 6.x: la 5.x e' l'ultima serie che pubblica il
// pacchetto UMD unico (`dist/maplibre-gl.js`, worker incluso) caricabile con un
// semplice <script> da un altro dominio; la 6.x e' solo ESM con il worker in un
// modulo separato, che il browser rifiuta di avviare da un'origine diversa.
//
// Una sola richiesta per sessione di pagina: chi chiama mentre il caricamento e'
// in corso riceve la STESSA promessa; a caricamento riuscito `window.maplibregl`
// resta e le aperture successive non toccano la rete. Se il caricamento fallisce
// (rete, CDN, impronta) il tag <script> viene tolto e la promessa dimenticata:
// «Riprova» ricomincia da capo invece di restare appesa a un errore.

export const MAPLIBRE_VERSION = '5.24.0';
export const MAPLIBRE_JS_URL = `https://unpkg.com/maplibre-gl@${MAPLIBRE_VERSION}/dist/maplibre-gl.js`;
export const MAPLIBRE_CSS_URL = `https://unpkg.com/maplibre-gl@${MAPLIBRE_VERSION}/dist/maplibre-gl.css`;
// sha384 dei file del tarball npm maplibre-gl-5.24.0.tgz (unpkg li serve identici)
export const MAPLIBRE_JS_SRI = 'sha384-5+cfbwT0iiub6VsQAdn6yz16nr6sDiQoHx6tm4O8OVYXHYOxcffFmCJBL0dgdvGp';
export const MAPLIBRE_CSS_SRI = 'sha384-uTttxo/aOKbdE5RlD/SPzSDoDmNvGlUYPjONi2MN/b7c9HPSvW07OIuyP7uL6jxK';
export const LOAD_TIMEOUT_MS = 20000;

let inCorso = null;

function libreria() {
  const ml = globalThis.maplibregl;
  return ml && typeof ml.Map === 'function' ? ml : null;
}

function assicuraCss() {
  if (document.querySelector('link[data-maplibre-css]')) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = MAPLIBRE_CSS_URL;
  link.integrity = MAPLIBRE_CSS_SRI;
  link.crossOrigin = 'anonymous';
  link.setAttribute('data-maplibre-css', '');
  document.head.appendChild(link);
}

/** La libreria MapLibre (`window.maplibregl`), caricata al massimo una volta. */
export function loadMapLibre({ timeoutMs = LOAD_TIMEOUT_MS } = {}) {
  const pronta = libreria();
  if (pronta) return Promise.resolve(pronta);
  if (inCorso) return inCorso;
  inCorso = new Promise((resolve, reject) => {
    assicuraCss();
    const script = document.createElement('script');
    script.src = MAPLIBRE_JS_URL;
    script.integrity = MAPLIBRE_JS_SRI;
    script.crossOrigin = 'anonymous';
    script.async = true;
    script.setAttribute('data-maplibre-js', '');
    let finito = false;
    const fine = (errore) => {
      if (finito) return;
      finito = true;
      clearTimeout(timer);
      const ml = errore ? null : libreria();
      inCorso = null;              // d'ora in poi risponde `window.maplibregl`
      if (ml) {
        resolve(ml);
        return;
      }
      script.remove();
      reject(errore || new Error('Libreria della mappa non disponibile'));
    };
    const timer = setTimeout(() => fine(new Error('Tempo scaduto nel caricamento della mappa')), timeoutMs);
    script.addEventListener('load', () => fine(null));
    script.addEventListener('error', () => fine(new Error('Impossibile scaricare la libreria della mappa')));
    document.head.appendChild(script);
  });
  return inCorso;
}
