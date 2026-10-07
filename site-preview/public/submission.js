/* Request identity and receipt recovery shared by the copied public forms. */
(() => {
  const prefix = 'stima360:submission:v1:';
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const proof = /^[0-9a-f]{64}$/;

  function read(requestId) {
    if (!uuid.test(requestId || '')) throw new Error('Identità della richiesta non valida');
    const raw = localStorage.getItem(prefix + requestId);
    if (!raw) return null;
    const record = JSON.parse(raw);
    if (record.version !== 1 || record.request_id !== requestId || !proof.test(record.receipt_key || '')
        || !['quick', 'detail'].includes(record.kind) || typeof record.scope !== 'string') {
      throw new Error('Ricevuta locale non valida');
    }
    return record;
  }

  function store(record) {
    const raw = JSON.stringify(record);
    localStorage.setItem(prefix + record.request_id, raw);
    if (localStorage.getItem(prefix + record.request_id) !== raw) {
      throw new Error('Impossibile conservare la ricevuta della richiesta');
    }
    return record;
  }

  function create(kind, scope) {
    if (!['quick', 'detail'].includes(kind) || typeof scope !== 'string' || !scope) {
      throw new Error('Richiesta non valida');
    }
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    return store({ version: 1, request_id: crypto.randomUUID(),
      receipt_key: Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join(''),
      kind, scope, payload: null, completed: false });
  }

  function requireRecord(intent) {
    const record = read(intent.request_id);
    if (!record || record.receipt_key !== intent.receipt_key || record.kind !== intent.kind || record.scope !== intent.scope) {
      throw new Error('La richiesta non è disponibile in questo browser');
    }
    return record;
  }

  function setPageIdentity(requestId) {
    const page = new URL(location.href);
    page.searchParams.set('request_id', requestId);
    history.replaceState(history.state, '', page);
  }

  function quickIntent(draft) {
    const requestId = new URLSearchParams(location.search).get('request_id');
    const scope = String(draft.id || draft.stima_uuid || 'direct-personal');
    let intent;
    if (requestId) {
      intent = read(requestId);
      if (!intent || intent.kind !== 'quick' || intent.scope !== scope) {
        throw new Error('La richiesta non è disponibile in questo browser');
      }
    } else {
      intent = create('quick', scope);
      setPageIdentity(intent.request_id);
    }
    return intent;
  }

  async function detailIntent(token, fresh = false) {
    const pointer = prefix + 'detail:' + token;
    const select = () => {
      const supplied = new URLSearchParams(location.search).get('request_id');
      const requestId = fresh ? null : supplied || localStorage.getItem(pointer);
      let intent = requestId ? read(requestId) : null;
      if (supplied && !fresh && !intent) throw new Error('La richiesta non è disponibile in questo browser');
      if (intent && (intent.kind !== 'detail' || intent.scope !== token)) throw new Error('Ricevuta non collegata al dettaglio');
      if (!intent) intent = create('detail', token);
      localStorage.setItem(pointer, intent.request_id);
      if (localStorage.getItem(pointer) !== intent.request_id) throw new Error('Impossibile conservare la ricevuta');
      setPageIdentity(intent.request_id);
      return intent;
    };
    // A shared token opened concurrently must initialise one local request and
    // proof. Existing copied URLs already identify the persisted request.
    if (navigator.locks) return navigator.locks.request(pointer, select);
    if (!fresh && (new URLSearchParams(location.search).get('request_id') || localStorage.getItem(pointer))) return select();
    throw new Error('Il browser non consente il recupero sicuro della richiesta');
  }

  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
    }
    return value;
  }

  function freeze(intent, payload) {
    const record = requireRecord(intent);
    const candidate = JSON.parse(JSON.stringify(payload));
    if (record.payload !== null) {
      if (JSON.stringify(canonical(record.payload)) !== JSON.stringify(canonical(candidate))) {
        throw new Error('Questa richiesta contiene già dati diversi. Avvia una nuova valutazione intenzionale.');
      }
      return record.payload;
    }
    record.payload = candidate;
    store(record); // A failed write stops the first network dispatch.
    return record.payload;
  }

  function headers(intent, json = false) {
    const record = requireRecord(intent);
    return { 'Idempotency-Key': record.request_id, 'X-Receipt-Key': record.receipt_key,
      ...(json ? { 'Content-Type': 'application/json' } : {}) };
  }

  function validateEnvelope(intent, body, status) {
    const receipt = body?.receipt;
    if (!receipt || receipt.request_id !== intent.request_id || receipt.kind !== intent.kind
        || !['received', 'completed', 'partial', 'attention'].includes(receipt.status)
        || typeof receipt.resumable !== 'boolean' || !receipt.steps || typeof receipt.steps !== 'object'
        || Array.isArray(receipt.steps)
        || (receipt.status === 'completed' && (receipt.resumable || status !== 200))) {
      throw new Error('Risposta senza una ricevuta verificabile');
    }
    if (receipt.stima_id !== null && receipt.stima_id !== undefined
        && (!Number.isSafeInteger(receipt.stima_id) || receipt.stima_id <= 0)) {
      throw new Error('Ricevuta senza collegamento valido alla stima');
    }
    // Server certification that a stored result is readable even though an
    // accessory notification is still uncertain or failed.
    if (receipt.result_available !== undefined && typeof receipt.result_available !== 'boolean') {
      throw new Error('Risposta senza una ricevuta verificabile');
    }
    if (receipt.result_available === true && (receipt.kind !== 'quick'
        || !['completed', 'attention', 'partial'].includes(receipt.status)
        || !Number.isSafeInteger(receipt.stima_id) || receipt.stima_id <= 0)) {
      throw new Error('Risposta senza una ricevuta verificabile');
    }
    return body;
  }

  async function response(intent, result, allowMissing = false) {
    if (result.status === 404 && allowMissing) return null;
    if (![200, 202].includes(result.status)) {
      const error = new Error('Ricevuta non disponibile o richiesta non autorizzata');
      error.status = result.status;
      throw error;
    }
    return validateEnvelope(intent, await result.json(), result.status);
  }

  async function receipt(intent) {
    const result = await fetch(Stima360Preview.apiUrl('/api/submissions/' + intent.request_id), {
      headers: headers(intent), cache: 'no-store'
    });
    return response(intent, result, true);
  }

  async function submit(intent, payload) {
    const frozen = freeze(intent, payload);
    const isQuick = intent.kind === 'quick';
    const body = isQuick ? JSON.stringify(frozen) : Object.entries(frozen).reduce((form, [key, value]) => {
      form.append(key, value);
      return form;
    }, new FormData());
    const result = await fetch(Stima360Preview.apiUrl(isQuick ? '/api/salva_stima' : '/api/salva_stima_dettagliata'), {
      method: 'POST', headers: headers(intent, isQuick), body, cache: 'no-store'
    });
    return response(intent, result);
  }

  async function resume(intent) {
    const record = requireRecord(intent);
    if (!record.payload) throw new Error('Richiesta originale non disponibile');
    const result = await fetch(Stima360Preview.apiUrl('/api/submissions/' + intent.request_id + '/resume'), {
      method: 'POST', headers: headers(intent), cache: 'no-store'
    });
    return response(intent, result);
  }

  function complete(intent) {
    const record = requireRecord(intent);
    const first = !record.completed;
    record.completed = true;
    store(record);
    return first;
  }

  window.Stima360Submission = Object.freeze({ create, quickIntent, detailIntent, freeze,
    receipt, submit, resume, complete, record: requireRecord });
})();
