// STIMA360 — operator-session.js
//
// P26-5: l'autenticazione a sessione operatore per i cinque frontend
// amministrativi legacy (CORE, PROPERTY, BUY, MATCH, FLOW).
//
// PERCHE' UNO SOLO E NON CINQUE COPIE
//
// I cinque avevano gia' lo stesso blocco di autenticazione copiato cinque
// volte: `encodeBasic`, `credentials` in memoria, `login` che verificava con
// /api/admin/check e poi teneva la password per rimetterla in un header a ogni
// richiesta. Cinque copie di una regola di sicurezza sono cinque posti dove
// sbagliarla, e quando P26-3 ha cambiato la regola nella OS Shell questi sono
// rimasti indietro senza che nulla lo segnalasse. Da qui in avanti la regola
// sta in un posto, e i cinque la chiamano.
//
// COSA CAMBIA RISPETTO AL BASIC
//
// Prima: username e password vivevano in una variabile per tutta la sessione e
// ogni richiesta li riesponeva in un header. Adesso la password viene inviata
// UNA volta a /api/operator-auth/login e non viene conservata da nessuna
// parte; il server risponde 204 e mette il token in un cookie HttpOnly, che
// questo file non puo' leggere ne' copiare - ed e' esattamente il punto.
//
// L'agenzia non viene mai decisa qui. Arriva da /me, che la legge dalla
// sessione lato server. Non esiste modo, in questo file, di chiederne un'altra.
//
// Script classico e non modulo ES: i cinque `app.js` sono caricati con un
// `<script src>` semplice, senza build. Espone `window.OperatorSession`.

(function () {
  'use strict';

  var session = null;
  var listeners = [];

  // `credentials: 'include'` su OGNI chiamata: senza, il browser non manda il
  // cookie e non accetta il Set-Cookie della login.
  function withCookie(options) {
    var merged = {};
    var source = options || {};
    for (var key in source) {
      if (Object.prototype.hasOwnProperty.call(source, key)) merged[key] = source[key];
    }
    merged.credentials = 'include';
    return merged;
  }

  function notify() {
    for (var i = 0; i < listeners.length; i += 1) listeners[i](session);
  }

  function call(path, options) {
    return fetch(path, withCookie(options)).catch(function () {
      throw new Error('Impossibile contattare il server. Verifica la connessione.');
    });
  }

  /**
   * Ripristina la sessione dal cookie che il browser gia' possiede.
   *
   * Chiamata all'avvio. Un 401 qui e' normale - vuol dire che non c'e'
   * sessione, o che e' scaduta o revocata - e non e' un errore da mostrare: si
   * finisce semplicemente sulla schermata di login.
   */
  function restore() {
    return call('/api/operator-auth/me', { method: 'GET' }).then(function (response) {
      if (response.status === 401) {
        session = null;
        notify();
        return null;
      }
      if (!response.ok) {
        session = null;
        notify();
        throw new Error('Servizio di autenticazione non disponibile.');
      }
      return response.json().then(function (data) {
        session = data;
        notify();
        return session;
      });
    });
  }

  /**
   * Login con email e password.
   *
   * La password esiste solo dentro questa funzione: viene serializzata nel
   * corpo della richiesta e poi esce dallo scope. Non viene salvata, non viene
   * passata ad altri moduli e non compare in nessun header successivo.
   */
  function login(email, password) {
    return call('/api/operator-auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, password: password })
    }).then(function (response) {
      if (response.status === 401) throw new Error('Credenziali non valide.');
      if (!response.ok) throw new Error('Servizio di autenticazione non disponibile.');
      // La login risponde 204 e non dice CHI ha autenticato: il token sta nel
      // cookie e nient'altro. L'identita' si chiede a /me, che e' la sola fonte.
      return restore();
    });
  }

  /**
   * Logout vero: il server revoca la sessione e cancella il cookie.
   *
   * Lo stato locale viene azzerato comunque, anche se la chiamata fallisce: se
   * il server non e' raggiungibile la cosa giusta e' riportare l'utente alla
   * schermata di login, non lasciarlo davanti a una UI che sembra attiva.
   */
  function logout() {
    return call('/api/operator-auth/logout', { method: 'POST' })
      .catch(function () { /* vedi sopra */ })
      .then(function () {
        session = null;
        notify();
      });
  }

  /**
   * Chiamata dal client HTTP quando una richiesta torna 401.
   *
   * Azzera lo stato e notifica, senza rifare una chiamata di rete: e' gia' un
   * 401 ad averla provocata, e ritentare da qui e' il modo classico di
   * costruire un ciclo login/401/login. Idempotente.
   */
  function sessionExpired() {
    if (session === null) return;
    session = null;
    notify();
  }

  /**
   * L'unico `fetch` autenticato dei cinque frontend.
   *
   * NESSUN header Authorization, in nessuna forma. Il cookie viaggia da solo
   * grazie a `credentials: 'include'`, e un 401 azzera la sessione e rilancia
   * senza ritentare.
   */
  function authFetch(path, options) {
    var settings = options || {};
    var headers = { 'Content-Type': 'application/json' };
    var supplied = settings.headers || {};
    for (var key in supplied) {
      if (Object.prototype.hasOwnProperty.call(supplied, key)) headers[key] = supplied[key];
    }
    var merged = withCookie(settings);
    merged.headers = headers;

    return fetch(path, merged).catch(function () {
      throw new Error('Impossibile contattare il server. Verifica la connessione.');
    }).then(function (response) {
      if (response.status === 401) {
        sessionExpired();
        var unauthorized = new Error('Sessione scaduta. Effettua di nuovo il login.');
        unauthorized.status = 401;
        throw unauthorized;
      }
      return response;
    });
  }

  window.OperatorSession = {
    login: login,
    logout: logout,
    restore: restore,
    sessionExpired: sessionExpired,
    authFetch: authFetch,
    getSession: function () { return session; },
    isAuthenticated: function () { return session !== null; },
    onAuthChange: function (fn) {
      listeners.push(fn);
      return function () {
        listeners = listeners.filter(function (registered) { return registered !== fn; });
      };
    }
  };
})();
