/* LMC-6 - "La Mia Casa": tutte le decisioni di cosa mostrare, in un posto solo.
 *
 * PERCHE' UN MODULO A PARTE. Il portale e' una IIFE che tocca il DOM alla
 * prima riga: utile, ma impossibile da interrogare senza un browser. Le cose
 * che LMC-6 deve garantire, pero', sono quasi tutte decisioni - se mostrare
 * un valore, se mostrare una percentuale, se mostrare una sezione - e una
 * decisione va provata eseguendola, non cercando una stringa nel sorgente.
 *
 * Qui dentro non si tocca il DOM, non si fa rete, non si legge il tempo
 * corrente: entra la risposta dell'API, esce un oggetto che dice cosa si
 * vede. Il file gira sia nel browser (window.OwnerHomeViewModel) sia in Node
 * (require), senza build e senza dipendenze.
 *
 * QUI NON SI CALCOLA NIENTE. Percentuali, variazioni, fasce della domanda e
 * valore corrente arrivano gia' decisi da LMC-2/3/4. Questo modulo formatta e
 * sceglie cosa passare; se un numero non c'e', la risposta e' l'assenza del
 * campo, mai uno zero.
 */
(function (factory) {
  'use strict';
  var api = factory();
  /* Prima il browser, poi CommonJS, e l'ordine conta: Node espone `module`
   * anche dentro `vm.runInThisContext`, quindi cercare CommonJS per primo
   * farebbe sparire il modulo dall'armatura DOM di `test_owner_06_p6`, che
   * lo aspetta su `self`. `self` invece in Node non esiste finche' non lo si
   * definisce, quindi un `require()` normale continua a ricevere l'export. */
  if (typeof self !== 'undefined' && self) {
    self.OwnerHomeViewModel = api;
  } else if (typeof module === 'object' && module && module.exports) {
    module.exports = api;
  }
})(function () {
  'use strict';

  var TITLE = 'La Mia Casa';
  var BUILDING_HISTORY = 'Storico in costruzione';
  var DEMAND_UNAVAILABLE = 'Domanda non ancora disponibile';

  /* Il metodo e' cambiato fra i due estremi: i valori restano veri entrambi,
   * ma la differenza non e' piu' solo mercato. Si mostra la frase al posto
   * della percentuale, che sarebbe la somma di due cose diverse. */
  var METHODOLOGY_NOTE =
    'Il metodo di valutazione è stato aggiornato; i due valori non sono ' +
    'direttamente confrontabili come puro andamento di mercato.';

  /* Il profilo invita a completare i dati SENZA promettere che il numero
   * diventera' piu' preciso: il motore non usa tutti i campi considerati, e
   * prometterlo sarebbe una garanzia che nessuno ha dato. */
  var PROFILE_NOTE =
    'Più informazioni avremo sulla casa, più completo potrà diventare il monitoraggio.';

  var WINDOWS = [
    { key: 'change_30d', label: '30 giorni' },
    { key: 'change_90d', label: '90 giorni' },
    { key: 'change_365d', label: '12 mesi' }
  ];

  var STATUS_LABELS = {
    ready: 'Monitoraggio attivo',
    building_history: 'Monitoraggio in avvio',
    partial: 'Dati incompleti'
  };

  // -- formattazione ------------------------------------------------------

  function isObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function isNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  function text(value) {
    if (value === null || value === undefined) {
      return null;
    }
    var stringa = String(value).trim();
    return stringa === '' ? null : stringa;
  }

  /* Un importo o niente. `0` non e' un valore da mostrare al posto di un
   * valore mancante, ed e' l'errore che questa funzione esiste per evitare. */
  function euro(value) {
    if (!isNumber(value) || value <= 0) {
      return null;
    }
    try {
      return new Intl.NumberFormat('it-IT', {
        style: 'currency', currency: 'EUR', maximumFractionDigits: 0
      }).format(value);
    } catch (_error) {
      return String(Math.round(value)) + ' €';
    }
  }

  function day(value) {
    var grezzo = text(value);
    if (grezzo === null) {
      return null;
    }
    var momento = new Date(grezzo.replace(' ', 'T'));
    if (isNaN(momento.getTime())) {
      return null;
    }
    var due = function (n) { return (n < 10 ? '0' : '') + n; };
    return due(momento.getUTCDate()) + '/' + due(momento.getUTCMonth() + 1) +
      '/' + momento.getUTCFullYear();
  }

  /* La percentuale arriva gia' calcolata dal backend: qui si sceglie solo il
   * segno e la virgola italiana. */
  function percent(value) {
    if (!isNumber(value)) {
      return null;
    }
    var segno = value > 0 ? '+' : '';
    return segno + value.toFixed(2).replace('.', ',') + '%';
  }

  function integer(value) {
    return typeof value === 'number' && isFinite(value) && Math.floor(value) === value
      ? String(value) : null;
  }

  function joined(parts, separator) {
    var puliti = [];
    for (var i = 0; i < parts.length; i += 1) {
      var pezzo = text(parts[i]);
      if (pezzo !== null) {
        puliti.push(pezzo);
      }
    }
    return puliti.length ? puliti.join(separator) : null;
  }

  function address(source) {
    var via = joined([source.via, source.civico], ' ');
    return joined([via, source.microzona, source.comune], ', ');
  }

  function sizeLine(source) {
    var mq = isNumber(source.mq) ? String(source.mq) + ' m²' : null;
    return joined([source.tipologia, mq], ' · ');
  }

  // -- la dashboard: quali sezioni accendere -------------------------------

  /* Il caso che LMC-6 corregge: fino a ieri zero properties significava
   * "nessun immobile", anche per chi ha una casa in monitoraggio. Sono due
   * superfici diverse e possono esistere insieme; il vuoto e' solo quando
   * non c'e' ne' l'una ne' l'altra. */
  function dashboardSections(payload) {
    var dati = isObject(payload) ? payload : {};
    var homes = Array.isArray(dati.homes) ? dati.homes.length : 0;
    var properties = Array.isArray(dati.properties) ? dati.properties.length : 0;
    return {
      showHomes: homes > 0,
      showProperties: properties > 0,
      showEmpty: homes === 0 && properties === 0,
      homeCount: homes,
      propertyCount: properties
    };
  }

  // -- la lista delle case -------------------------------------------------

  function homeCard(home) {
    var dati = isObject(home) ? home : {};
    var indirizzo = address(dati);
    return {
      stimaId: isNumber(dati.stima_id) ? dati.stima_id : null,
      title: indirizzo || joined([dati.tipologia, dati.comune], ' · ') || TITLE,
      subtitle: sizeLine(dati),
      initialValue: euro(dati.initial_value),
      initialLabel: 'Valore iniziale',
      statusLabel: STATUS_LABELS[dati.data_status] || null,
      hasWatch: dati.has_watch === true
    };
  }

  // -- il dettaglio --------------------------------------------------------

  function valueSection(valuation) {
    var dati = isObject(valuation) ? valuation : {};
    var corrente = euro(dati.current_value);
    return {
      initialLabel: 'Valore iniziale',
      initialValue: euro(dati.initial_value),
      currentLabel: 'Valore monitorato',
      currentValue: corrente,
      hasCurrent: corrente !== null,
      computedAtLabel: 'Calcolato il',
      computedAt: day(dati.current_value_computed_at),
      buildingHistory: corrente === null,
      buildingMessage: corrente === null ? BUILDING_HISTORY : null
    };
  }

  function historyPoints(list) {
    var punti = [];
    if (!Array.isArray(list)) {
      return punti;
    }
    for (var i = 0; i < list.length; i += 1) {
      var voce = list[i];
      if (!isObject(voce) || !isNumber(voce.price_exact)) {
        continue;
      }
      var etichetta = day(voce.computed_at);
      if (etichetta === null) {
        continue;
      }
      /* Campo per campo: cio' che non e' scritto qui non arriva in pagina,
       * nemmeno se l'API domani aggiungesse una chiave allo snapshot. */
      punti.push({
        value: voce.price_exact,
        valueText: euro(voce.price_exact),
        dateLabel: etichetta
      });
    }
    return punti;
  }

  function changeRows(valuation) {
    var dati = isObject(valuation) ? valuation : {};
    var righe = [];
    for (var i = 0; i < WINDOWS.length; i += 1) {
      var finestra = WINDOWS[i];
      var cambio = dati[finestra.key];
      /* `null` significa "la storia non basta per dirlo": la riga non
       * esiste, non vale zero e non si mostra vuota. */
      if (!isObject(cambio)) {
        continue;
      }
      var cambiato = cambio.methodology_changed === true;
      righe.push({
        key: finestra.key,
        label: finestra.label,
        fromValue: euro(cambio.from_value),
        toValue: euro(cambio.to_value),
        fromDate: day(cambio.from_computed_at),
        toDate: day(cambio.to_computed_at),
        percentText: cambiato ? null : percent(cambio.change_percent),
        methodologyChanged: cambiato,
        note: cambiato ? METHODOLOGY_NOTE : null
      });
    }
    return righe;
  }

  function historySection(detail) {
    var capability = isObject(detail.capabilities) &&
      detail.capabilities.valuation_history === true;
    if (!capability) {
      return { available: false, message: BUILDING_HISTORY, points: [], changes: [] };
    }
    return {
      available: true,
      message: null,
      points: historyPoints(detail.valuation_history),
      changes: changeRows(detail.valuation)
    };
  }

  function demandSection(detail) {
    var capability = isObject(detail.capabilities) &&
      detail.capabilities.buyer_demand === true;
    var blocco = isObject(detail.buyer_demand) ? detail.buyer_demand : {};
    if (!capability) {
      return {
        available: false,
        message: DEMAND_UNAVAILABLE,
        status: null, label: null, compatibleText: null, recentText: null,
        updatedAt: null, disclaimer: text(blocco.disclaimer)
      };
    }
    /* Etichetta e messaggio arrivano dal backend e si mostrano come sono: le
     * fasce le decide LMC-4, e una seconda tabella di soglie qui sarebbe una
     * seconda verita'. */
    return {
      available: true,
      status: text(blocco.status),
      label: text(blocco.label),
      message: text(blocco.message),
      compatibleLabel: 'Richieste compatibili',
      compatibleText: integer(blocco.compatible_requests),
      recentLabel: 'Di cui recenti',
      recentText: integer(blocco.recent_compatible_requests),
      recencyDays: integer(blocco.recency_days),
      updatedAtLabel: 'Aggiornato il',
      updatedAt: day(blocco.updated_at),
      disclaimer: text(blocco.disclaimer)
    };
  }

  function profileSection(detail) {
    var profilo = isObject(detail.profile) ? detail.profile : {};
    var conosciuti = Array.isArray(profilo.known_fields) ? profilo.known_fields.slice() : [];
    var mancanti = Array.isArray(profilo.missing_fields) ? profilo.missing_fields.slice() : [];
    var percentuale = integer(profilo.completion_percent);
    return {
      title: 'Profilo casa',
      percentText: percentuale === null ? null : percentuale + '%',
      known: conosciuti,
      missing: mancanti,
      note: PROFILE_NOTE,
      /* LMC-10: il form esiste quando il backend dichiara la capability.
       * Resta il backend a decidere, non questo modulo. */
      editable: isObject(detail.capabilities) && detail.capabilities.profile_update === true,
      /* LMC10_START
       * La versione su cui il form viene aperto, e che tornera' indietro come
       * `expected_version`. Zero quando il backend non la dichiara: e' il
       * valore di "nessuna correzione ancora", cioe' quello giusto per la
       * prima modifica, e non un ripiego inventato. */
      version: isNumber(detail.profile_version) ? detail.profile_version : 0,
      /* L'elenco dei campi modificabili arriva dal backend e non e' scritto
       * qui: due whitelist che devono coincidere e che nessuno confronta
       * prima o poi non coincidono. */
      editableFields: Array.isArray(profilo.editable_fields)
        ? profilo.editable_fields.slice() : [],
      overriddenFields: Array.isArray(profilo.overridden_fields)
        ? profilo.overridden_fields.slice() : [],
      updatedAt: day(profilo.updated_at),
      /* I valori con cui precompilare il form: il PROFILO EFFETTIVO, cioe'
       * esattamente cio' che la scheda sta mostrando. Prenderli da un'altra
       * parte vorrebbe dire aprire il form su numeri diversi da quelli che
       * il proprietario ha appena letto. */
      values: isObject(detail.property) ? detail.property : {}
      /* LMC10_END */
    };
  }

  function homeDetail(detail) {
    var dati = isObject(detail) ? detail : {};
    var immobile = isObject(dati.property) ? dati.property : {};
    return {
      stimaId: isNumber(dati.stima_id) ? dati.stima_id : null,
      header: {
        title: TITLE,
        address: address(immobile),
        summary: sizeLine(immobile)
      },
      value: valueSection(dati.valuation),
      history: historySection(dati),
      demand: demandSection(dati),
      profile: profileSection(dati)
      /* Nessuna chiave per i comparabili: LMC-5 ha chiuso la fonte dati, e
       * una sezione che non si puo' riempire non si dichiara nemmeno. */
    };
  }

  return {
    TITLE: TITLE,
    BUILDING_HISTORY: BUILDING_HISTORY,
    DEMAND_UNAVAILABLE: DEMAND_UNAVAILABLE,
    METHODOLOGY_NOTE: METHODOLOGY_NOTE,
    PROFILE_NOTE: PROFILE_NOTE,
    WINDOWS: WINDOWS,
    euro: euro,
    day: day,
    percent: percent,
    dashboardSections: dashboardSections,
    homeCard: homeCard,
    homeDetail: homeDetail
  };
});
