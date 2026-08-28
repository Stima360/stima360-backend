const api='/api/buy';
const BUY_STATUSES=['draft','active','paused','satisfied','closed'];
const BUY_URGENCIES=['exploratory','flexible','within_6_months','within_3_months','immediate'];
const REQUIREMENT_LEVELS=['required','preferred','optional','excluded'];
const FEATURE_VALUE_TYPES=['boolean','number','range','text'];
const MATCH_NUMBER_FIELDS=['budget_min','budget_target','budget_max','budget_flexibility_percent','surface_min','surface_target','surface_max'];
const MATCH_INTEGER_FIELDS=['rooms_min','bedrooms_min','bathrooms_min'];
const CHILD_KINDS=['locations','typologies','features'];

let current=null;
let credentials=null;
let actionSubmitPending=false;
let proposalSubmitPending=false;

const $=selector=>document.querySelector(selector);
const money=value=>value==null?'—':new Intl.NumberFormat('it-IT',{style:'currency',currency:'EUR',maximumFractionDigits:0}).format(value);
const number=value=>value==null?'—':new Intl.NumberFormat('it-IT',{maximumFractionDigits:2}).format(value);
const dt=value=>value?new Date(value).toLocaleString('it-IT',{dateStyle:'short',timeStyle:'short'}):'—';
const esc=value=>String(value??'').replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));

function positiveId(value){
  const n=Number(value);
  return Number.isInteger(n)&&n>0?n:null;
}

function numericValue(value,label,{integer=false,min=null,max=null}={}){
  if(value===undefined||value===null||String(value).trim()==='')return null;
  const parsed=Number(value);
  if(!Number.isFinite(parsed)||(integer&&!Number.isInteger(parsed)))throw new Error(`${label} non valido`);
  if(min!==null&&parsed<min)throw new Error(`${label} deve essere almeno ${min}`);
  if(max!==null&&parsed>max)throw new Error(`${label} non può superare ${max}`);
  return parsed;
}

function flagValue(value){
  return value===true||value==='true'||value==='on'||value===1;
}

function validateOrderedRange(payload,fields){
  for(let leftIndex=0;leftIndex<fields.length-1;leftIndex+=1){
    for(let rightIndex=leftIndex+1;rightIndex<fields.length;rightIndex+=1){
      const left=fields[leftIndex];
      const right=fields[rightIndex];
      if(payload[left]!==null&&payload[right]!==null&&payload[left]>payload[right]){
        throw new Error(`${left} cannot exceed ${right}`);
      }
    }
  }
}

function buildBuyRequestPayload(values,isCreate=false){
  const status=values.status||'draft';
  const urgency=values.urgency||'flexible';
  if(!BUY_STATUSES.includes(status))throw new Error('status non valido');
  if(!BUY_URGENCIES.includes(urgency))throw new Error('urgency non valida');

  const payload={status,urgency};
  for(const field of MATCH_NUMBER_FIELDS){
    const maximum=field==='budget_flexibility_percent'?100:null;
    let value=numericValue(values[field],field,{min:0,max:maximum});
    if(field==='budget_flexibility_percent'&&value===null&&isCreate)value=0;
    payload[field]=value;
  }
  for(const field of MATCH_INTEGER_FIELDS){
    payload[field]=numericValue(values[field],field,{integer:true,min:0});
  }

  validateOrderedRange(payload,['budget_min','budget_target','budget_max']);
  validateOrderedRange(payload,['surface_min','surface_target','surface_max']);

  if(isCreate){
    const contactId=positiveId(values.contact_id);
    if(contactId===null)throw new Error('ID contatto non valido');
    const title=String(values.title||'').trim();
    if(!title)throw new Error('Titolo obbligatorio');
    payload.contact_id=contactId;
    payload.lead_id=values.lead_id===undefined||values.lead_id===null||String(values.lead_id).trim()===''?null:positiveId(values.lead_id);
    if(values.lead_id!==undefined&&values.lead_id!==null&&String(values.lead_id).trim()!==''&&payload.lead_id===null)throw new Error('ID lead non valido');
    payload.title=title;
  }
  return payload;
}

function buildLocationPayload(values){
  const locationType=String(values.location_type||'');
  if(!['province','municipality','microzone'].includes(locationType))throw new Error('Livello località non valido');
  const locationValue=String(values.location_value||'').trim();
  if(!locationValue)throw new Error('Valore località obbligatorio');
  const priority=numericValue(values.priority,'priority',{integer:true,min:1,max:10});
  const isRequired=flagValue(values.is_required);
  const isExcluded=flagValue(values.is_excluded);
  if(isRequired&&isExcluded)throw new Error('Una località non può essere obbligatoria ed esclusa');
  return {location_type:locationType,[locationType]:locationValue,priority,is_required:isRequired,is_excluded:isExcluded};
}

function buildTypologyPayload(values){
  const propertyType=String(values.property_type||'').trim();
  const requirementLevel=String(values.requirement_level||'preferred');
  if(!propertyType)throw new Error('Tipologia obbligatoria');
  if(!REQUIREMENT_LEVELS.includes(requirementLevel))throw new Error('Livello requisito non valido');
  return {property_type:propertyType,requirement_level:requirementLevel};
}

function buildFeaturePayload(values){
  const featureCode=String(values.feature_code||'').trim();
  const requirementLevel=String(values.requirement_level||'preferred');
  const valueType=String(values.value_type||'boolean');
  if(!featureCode)throw new Error('Codice caratteristica obbligatorio');
  if(!REQUIREMENT_LEVELS.includes(requirementLevel))throw new Error('Livello requisito non valido');
  if(!FEATURE_VALUE_TYPES.includes(valueType))throw new Error('Tipo valore non valido');
  const payload={feature_code:featureCode,requirement_level:requirementLevel,value_type:valueType};
  if(valueType==='boolean'){
    if(values.value_boolean!=='true'&&values.value_boolean!=='false'&&values.value_boolean!==true&&values.value_boolean!==false)throw new Error('Valore booleano non valido');
    payload.value_boolean=values.value_boolean===true||values.value_boolean==='true';
  }else if(valueType==='number'||valueType==='range'){
    payload.value_min=numericValue(values.value_min,'value_min');
    payload.value_max=numericValue(values.value_max,'value_max');
    if(payload.value_min!==null&&payload.value_max!==null&&payload.value_min>payload.value_max)throw new Error('value_min cannot exceed value_max');
  }else{
    payload.value_text=String(values.value_text??'').trim();
    if(!payload.value_text)throw new Error('Valore testuale obbligatorio');
  }
  return payload;
}

function buildMatchDecisionPayload(values){
  const action=String(values.action||'');
  const allowed=['proposed','discarded','interested','visit_requested','visit_scheduled','visited','offer_candidate'];
  if(!allowed.includes(action))throw new Error('Azione non valida');
  const payload={action};
  for(const field of ['reason_code','notes']){
    if(values[field]!==undefined&&values[field]!==null&&String(values[field])!=='')payload[field]=String(values[field]);
  }
  if(action==='visit_scheduled'){
    const raw=String(values.scheduled_at||'').trim();
    if(!raw)throw new Error('Data e ora visita obbligatorie');
    const scheduledAt=new Date(raw);
    if(Number.isNaN(scheduledAt.getTime()))throw new Error('Data e ora visita non valide');
    payload.scheduled_at=scheduledAt.toISOString();
  }
  return payload;
}

function proposalDateTimeLocal(value){
  if(!value)return '';
  const parsed=new Date(value);
  if(Number.isNaN(parsed.getTime()))return '';
  return new Date(parsed.getTime()-parsed.getTimezoneOffset()*60000).toISOString().slice(0,16);
}

function proposalAmount(value){
  const amount=numericValue(value,'Importo',{min:0});
  if(amount===null||amount<=0)throw new Error('Importo deve essere maggiore di zero');
  return amount;
}

function proposalExpiry(value){
  const raw=String(value||'').trim();
  const expiry=new Date(raw);
  if(!raw||Number.isNaN(expiry.getTime()))throw new Error('Scadenza non valida');
  return expiry.toISOString();
}

function buildProposalCreatePayload(values){
  const matchId=positiveId(values.match_id);
  if(matchId===null)throw new Error('ID MATCH non valido');
  const idempotencyKey=String(values.idempotency_key||'').trim();
  if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(idempotencyKey))throw new Error('Chiave idempotenza non valida');
  return {
    match_id:matchId,
    amount:proposalAmount(values.amount),
    expires_at:proposalExpiry(values.expires_at),
    notes:String(values.notes||'').trim()||null,
    idempotency_key:idempotencyKey,
  };
}

function buildProposalUpdatePayload(values){
  return {
    amount:proposalAmount(values.amount),
    expires_at:proposalExpiry(values.expires_at),
    notes:String(values.notes||'').trim()||null,
  };
}

function proposalActions(proposal){
  if(proposal?.status==='draft')return ['edit','submitted','withdrawn'];
  if(proposal?.status==='submitted'){
    const actions=['accepted','rejected','withdrawn'];
    const expiresAt=new Date(proposal.expires_at);
    if(!Number.isNaN(expiresAt.getTime())&&expiresAt<=new Date())actions.push('expired');
    return actions;
  }
  return [];
}

function childCollectionUrl(kind,requestId){
  const id=positiveId(requestId);
  if(!CHILD_KINDS.includes(kind)||id===null)throw new Error('Relazione o richiesta non valida');
  return `${api}/requests/${id}/${kind}`;
}

function childItemUrl(kind,itemId){
  const id=positiveId(itemId);
  if(!CHILD_KINDS.includes(kind)||id===null)throw new Error('Relazione o criterio non valido');
  return `${api}/${kind}/${id}`;
}

function formValues(form){
  const values=Object.fromEntries(new FormData(form).entries());
  form.querySelectorAll('input[type="checkbox"]').forEach(input=>{values[input.name]=input.checked;});
  return values;
}

function toast(text){
  const element=$('#toast');
  element.textContent=text;
  element.style.display='block';
  setTimeout(()=>element.style.display='none',2600);
}

function encodeBasic(username,password){
  const bytes=new TextEncoder().encode(`${username}:${password}`);
  let binary='';
  for(const byte of bytes)binary+=String.fromCharCode(byte);
  return `Basic ${btoa(binary)}`;
}

function setLoginStatus(message=''){
  const node=document.getElementById('login-status');
  if(node)node.textContent=message;
}

function showLogin(message=''){
  document.getElementById('app-view').hidden=true;
  document.getElementById('login-view').hidden=false;
  setLoginStatus(message);
}

function showApp(){
  document.getElementById('login-view').hidden=true;
  document.getElementById('app-view').hidden=false;
  setLoginStatus('');
}

function logout(message=''){
  credentials=null;
  current=null;
  const form=document.getElementById('login-form');
  if(form)form.reset();
  showLogin(message);
}

async function login(event){
  event.preventDefault();
  const username=document.getElementById('admin-username').value;
  const password=document.getElementById('admin-password').value;
  setLoginStatus('Verifica credenziali…');
  try{
    const response=await fetch('/api/admin/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user:username,password:password})});
    if(!response.ok){
      setLoginStatus(response.status===401?'Credenziali non valide.':'Servizio amministrativo non disponibile.');
      return;
    }
    credentials={username,password};
    showApp();
    await Promise.all([dashboard(),load()]);
    await applyDeepLink();
  }catch(_error){
    setLoginStatus('Errore di connessione. Riprova.');
  }
}

async function req(url,opt={}){
  const headers={'Content-Type':'application/json',...(opt.headers||{})};
  if(credentials)headers.Authorization=encodeBasic(credentials.username,credentials.password);
  const response=await fetch(url,{...opt,headers});
  if(response.status===401){
    logout('Credenziali non valide.');
    throw new Error('Non autorizzato');
  }
  if(!response.ok)throw new Error(await response.text());
  return response.status===204?null:response.json();
}

function overdue(value){return value&&new Date(value)<new Date();}

async function dashboard(){
  const data=await req(api+'/dashboard');
  const interactions=data.interaction_counts||{};
  const cards=[['Attive',data.active],['Prioritarie',data.priority],['Azioni scadute',data.overdue_actions],['Proposti',interactions.proposed||0],['Visitati',interactions.visited||0],['Budget attivo',money(data.active_target_budget)]];
  $('#kpi').innerHTML=cards.map(item=>`<div class="card"><small>${esc(item[0])}</small><strong>${esc(item[1])}</strong></div>`).join('');
}

async function load(){
  const query=new URLSearchParams();
  if($('#search').value)query.set('search',$('#search').value);
  if($('#status').value)query.set('status',$('#status').value);
  if($('#priority').value)query.set('priority',$('#priority').value);
  const data=await req(api+'/requests?'+query);
  const items=Array.isArray(data.items)?data.items:[];
  const rows=items.map(item=>{
    const id=positiveId(item.id);
    if(id===null)return '';
    const matches=Number(item.matches_count)||0;
    const strong=Number(item.strong_matches_count)||0;
    return `<tr data-id="${id}"><td><b>${esc(item.title)}</b><br><small>#${id}</small></td><td>${esc(item.contact_name||item.contact_id)}</td><td><span class="badge">${esc(item.status)}</span></td><td>${strong} forti / ${matches}</td><td class="${overdue(item.next_action_at)?'overdue':''}">${esc(dt(item.next_action_at))}<br><small>${esc(item.next_action_note||'')}</small></td></tr>`;
  }).join('');
  $('#rows').innerHTML=rows||'<tr><td colspan="5">Nessuna richiesta</td></tr>';
  document.querySelectorAll('tr[data-id]').forEach(row=>{row.onclick=()=>detail(row.dataset.id);});
}

function matchClass(value){
  return ['excellent','strong'].includes(value)?'good':['weak','poor','incompatible'].includes(value)?'bad':'warn';
}

async function applyDeepLink(){
  const params=new URLSearchParams(window.location.search);
  const id=positiveId(params.get('id'));
  if(id===null)return;
  try{
    await detail(id);
  }catch(error){
    current=null;
    $('#detail-main').textContent='';
    $('#criteria-sections').hidden=true;
    toast('Richiesta non trovata: '+error.message);
  }
}

function renderMatchCriteria(request){
  const locations=document.getElementById('criteria-locations');
  const typologies=document.getElementById('criteria-typologies');
  const features=document.getElementById('criteria-features');

  function emptyNode(text){
    const empty=document.createElement('p');
    empty.className='muted';
    empty.textContent=text;
    return empty;
  }

  function rowNode(title,detailText,onDelete){
    const row=document.createElement('div');
    row.className='criterion-row';
    const content=document.createElement('div');
    const strong=document.createElement('strong');
    strong.textContent=title;
    const detail=document.createElement('small');
    detail.textContent=detailText;
    content.append(strong,document.createElement('br'),detail);
    const button=document.createElement('button');
    button.type='button';
    button.className='danger compact';
    button.textContent='Rimuovi';
    button.addEventListener('click',onDelete);
    row.append(content,button);
    return row;
  }

  const locationRows=(Array.isArray(request.locations)?request.locations:[]).map(item=>{
    const value=item.microzone||item.municipality||item.province||item.region||'—';
    const mode=item.is_excluded?'esclusa':item.is_required?'obbligatoria':'preferita';
    return rowNode(`${item.location_type||'località'}: ${value}`,`priorità ${item.priority??1} · ${mode}`,()=>deleteLocation(item.id));
  });
  locations.replaceChildren(...(locationRows.length?locationRows:[emptyNode('Nessuna località impostata.')]));

  const typologyRows=(Array.isArray(request.typologies)?request.typologies:[]).map(item=>rowNode(item.property_type||'—',item.requirement_level||'preferred',()=>deleteTypology(item.id)));
  typologies.replaceChildren(...(typologyRows.length?typologyRows:[emptyNode('Nessuna tipologia impostata.')]));

  const featureRows=(Array.isArray(request.features)?request.features:[]).map(item=>{
    let value='—';
    if(item.value_type==='boolean')value=item.value_boolean===true?'sì':item.value_boolean===false?'no':'—';
    else if(item.value_type==='text')value=item.value_text||'—';
    else value=`${item.value_min??'—'} – ${item.value_max??'—'}`;
    return rowNode(item.feature_code||'—',`${item.requirement_level||'preferred'} · ${item.value_type||'boolean'} · ${value}`,()=>deleteFeature(item.id));
  });
  features.replaceChildren(...(featureRows.length?featureRows:[emptyNode('Nessuna caratteristica impostata.')]));
}

async function detail(id){
  const requestId=positiveId(id);
  if(requestId===null)throw new Error('ID richiesta non valido');
  const [workflow,proposalData]=await Promise.all([
    req(api+'/requests/'+requestId+'/workflow'),
    req(`/api/proposals?buy_request_id=${requestId}`),
  ]);
  current={...workflow,proposals:Array.isArray(proposalData?.items)?proposalData.items:[]};
  const x=current;
  const matches=Array.isArray(x.matches)?x.matches:[];
  const proposals=Array.isArray(x.proposals)?x.proposals:[];
  const tasks=Array.isArray(x.tasks)?x.tasks:[];
  const history=Array.isArray(x.history)?x.history:[];
  const cid=positiveId(x.contact_id);
  const contactLabel=esc(x.contact_name||x.contact_id);
  const contactLink=cid?`<a href="/core-admin/?view=contact360&id=${cid}" target="_blank" rel="noopener noreferrer"><b>${contactLabel}</b></a>`:`<b>${contactLabel}</b>`;
  const matchCards=matches.map(m=>{
    const pid=positiveId(m.property_id);
    const mid=positiveId(m.id);
    const propertyLabel=esc(m.property_title||m.property_code);
    const propertyLink=pid?`<a href="/property-admin/?id=${pid}" target="_blank" rel="noopener noreferrer"><strong>${propertyLabel}</strong></a>`:`<strong>${propertyLabel}</strong>`;
    const score=Number.isFinite(Number(m.effective_score))?Math.round(Number(m.effective_score)):0;
    const matchLabel=`${score} · ${esc(m.match_class)}`;
    const matchLink=mid?`<a href="/match-admin/?id=${mid}" target="_blank" rel="noopener noreferrer"><span class="badge ${matchClass(m.match_class)}">${matchLabel}</span></a>`:`<span class="badge ${matchClass(m.match_class)}">${matchLabel}</span>`;
    const related=mid===null?[]:proposals.filter(item=>positiveId(item.match_id)===mid);
    const proposalCards=related.map(proposal=>{
      const proposalId=positiveId(proposal.id);
      if(proposalId===null)return '';
      const buttons=proposalActions(proposal).map(actionName=>actionName==='edit'
        ?`<button type="button" onclick="openProposal(${mid},${proposalId})">Modifica</button>`
        :`<button type="button" onclick="transitionProposal(${proposalId},'${actionName}')">${esc({submitted:'Invia',accepted:'Accetta',rejected:'Rifiuta',withdrawn:'Ritira',expired:'Segna scaduta'}[actionName])}</button>`
      ).join(' ');
      return `<div class="event"><b>${esc(money(proposal.amount))}</b> <span class="badge">${esc(proposal.status)}</span><br><small>Scadenza ${esc(dt(proposal.expires_at))}</small>${proposal.notes?`<div>${esc(proposal.notes)}</div>`:''}${buttons?`<div>${buttons}</div>`:''}</div>`;
    }).join('');
    const hasOpen=related.some(proposal=>['draft','submitted'].includes(proposal.status));
    const action=mid?`<button type="button" onclick="openAction(${mid})">Registra esito</button> ${hasOpen?'':`<button type="button" onclick="openProposal(${mid})">Crea proposta</button>`}`:'';
    return `<div class="match"><div class="row"><div>${propertyLink}<br><small>${esc([m.city,m.microzone].filter(Boolean).join(' · '))}</small></div>${matchLink}</div><p>${esc(money(m.asking_price))} · ${esc(m.commercial_status)}${m.last_interaction?` · ultimo: ${esc(m.last_interaction)}`:''}</p>${action}${proposalCards?`<div class="timeline">${proposalCards}</div>`:''}</div>`;
  }).join('')||'<p class="muted">Nessun match calcolato.</p>';
  const taskCards=tasks.map(task=>`<div class="match"><b>${esc(task.title)}</b> <span class="badge">${esc(task.status)}</span><br><small class="${overdue(task.due_at)&&!['completed','cancelled'].includes(task.status)?'overdue':''}">${esc(dt(task.due_at))} · ${esc(task.priority)}</small></div>`).join('')||'<p class="muted">Nessun task collegato.</p>';
  const historyCards=history.map(event=>`<div class="event"><b>${esc(event.event_type)}</b><br><small>${esc(dt(event.created_at))}${event.property_title?' · '+esc(event.property_title):''}</small><div>${esc(event.description||event.reason_code||'')}</div></div>`).join('')||'<p class="muted">Nessun evento.</p>';

  $('#detail-main').innerHTML=`<div class="row"><div><h2>${esc(x.title)}</h2><p><span class="badge">${esc(x.status)}</span> · ${esc(x.priority)} · ${esc(x.urgency)}</p></div><button type="button" onclick="editRequest()">Modifica criteri</button></div><p>${contactLink}<br>${esc(x.contact_email||'')}<br>${esc(x.contact_phone||'')}</p><div class="section"><h3>Criteri MATCH</h3><div class="criteria-summary"><p><b>Budget</b><br>${esc(money(x.budget_min))} / ${esc(money(x.budget_target))} / ${esc(money(x.budget_max))} · flessibilità ${esc(number(x.budget_flexibility_percent))}%</p><p><b>Superficie</b><br>${esc(number(x.surface_min))} / ${esc(number(x.surface_target))} / ${esc(number(x.surface_max))} m²</p><p><b>Minimi</b><br>${esc(number(x.rooms_min))} locali · ${esc(number(x.bedrooms_min))} camere · ${esc(number(x.bathrooms_min))} bagni</p></div></div><div class="section"><h3>Prossima azione</h3><p class="${overdue(x.next_action_at)?'overdue':''}">${esc(dt(x.next_action_at))} — ${esc(x.next_action_note||'Nessuna')}</p><button type="button" onclick="quickNextAction()">Imposta</button> <button type="button" onclick="openTask()">Crea task CORE</button></div><div class="section"><h3>Finanza</h3><p><b>${esc(x.finance_status)}</b> · Mutuo ${x.mortgage_required===true?'sì':x.mortgage_required===false?'no':'da definire'} · Pre-delibera ${x.mortgage_preapproved===true?'sì':'no/da definire'}</p><p>${esc(x.finance_notes||'')}</p></div><div class="section"><h3>Match reali (${matches.length})</h3>${matchCards}</div><div class="section"><h3>Task CORE (${tasks.length})</h3>${taskCards}</div><div class="section"><h3>Storico ricerca</h3><div class="timeline">${historyCards}</div></div>`;
  $('#criteria-sections').hidden=false;
  renderMatchCriteria(x);
}

function editRequest(){
  const requestId=positiveId(current?.id);
  if(requestId===null){toast('Seleziona una richiesta valida.');return;}
  const form=$('#editForm');
  for(const field of ['status','urgency',...MATCH_NUMBER_FIELDS,...MATCH_INTEGER_FIELDS]){
    const input=form.elements.namedItem(field);
    if(input)input.value=current[field]??'';
  }
  $('#editModal').showModal();
}

async function quickNextAction(){
  const requestId=positiveId(current?.id);
  if(requestId===null)return;
  const note=prompt('Prossima azione');
  if(!note)return;
  const when=prompt('Data/ora ISO, es. 2026-07-20T10:00:00+02:00');
  await req(api+'/requests/'+requestId,{method:'PATCH',body:JSON.stringify({next_action_note:note,next_action_at:when||null})});
  toast('Prossima azione salvata');
  await detail(requestId);
  await load();
  await dashboard();
}

function openAction(matchId){
  const id=positiveId(matchId);
  if(id===null)return;
  const match=current?.matches?.find(item=>positiveId(item.id)===id);
  const title=match?.property_title||match?.property_code||'Immobile';
  $('#actionTitle').textContent='Esito: '+title;
  $('#actionForm [name=match_id]').value=id;
  updateActionScheduleField();
  $('#actionModal').showModal();
}

function openProposal(matchId,proposalId=null){
  const validMatchId=positiveId(matchId);
  const validProposalId=proposalId===null?null:positiveId(proposalId);
  if(validMatchId===null||proposalId!==null&&validProposalId===null)return;
  const proposal=validProposalId===null?null:current?.proposals?.find(item=>positiveId(item.id)===validProposalId);
  if(validProposalId!==null&&!proposal)return;
  const form=$('#proposalForm');
  form.reset();
  form.elements.proposal_id.value=validProposalId??'';
  form.elements.match_id.value=validMatchId;
  form.elements.idempotency_key.value=proposal?'':crypto.randomUUID();
  form.elements.amount.value=proposal?.amount??'';
  form.elements.expires_at.value=proposalDateTimeLocal(proposal?.expires_at);
  form.elements.notes.value=proposal?.notes??'';
  $('#proposalTitle').textContent=proposal?'Modifica proposta':'Nuova proposta';
  $('#proposalModal').showModal();
}

async function transitionProposal(proposalId,targetStatus){
  const id=positiveId(proposalId);
  const requestId=positiveId(current?.id);
  if(id===null||requestId===null)return;
  try{
    await req(`/api/proposals/${id}/transition`,{method:'POST',body:JSON.stringify({target_status:targetStatus})});
    toast('Stato proposta aggiornato');
    await detail(requestId);
  }catch(error){toast('Errore: '+error.message);}
}

function openTask(){
  if(positiveId(current?.id)!==null)$('#taskModal').showModal();
}

function openLocation(){if(positiveId(current?.id)!==null)$('#locationModal').showModal();}
function openTypology(){if(positiveId(current?.id)!==null)$('#typologyModal').showModal();}
function openFeature(){if(positiveId(current?.id)!==null)$('#featureModal').showModal();}

async function addLocation(event){
  event.preventDefault();
  const requestId=positiveId(current?.id);
  if(requestId===null)return;
  try{
    const payload=buildLocationPayload(formValues(event.target));
    await req(childCollectionUrl('locations',requestId),{method:'POST',body:JSON.stringify(payload)});
    $('#locationModal').close();
    event.target.reset();
    toast('Località aggiunta');
    await detail(requestId);
  }catch(error){toast('Errore: '+error.message);}
}

async function deleteLocation(locationId){
  const id=positiveId(locationId);
  const requestId=positiveId(current?.id);
  if(id===null||requestId===null)return;
  try{
    await req(childItemUrl('locations',id),{method:'DELETE'});
    toast('Località rimossa');
    await detail(requestId);
  }catch(error){toast('Errore: '+error.message);}
}

async function addTypology(event){
  event.preventDefault();
  const requestId=positiveId(current?.id);
  if(requestId===null)return;
  try{
    const payload=buildTypologyPayload(formValues(event.target));
    await req(childCollectionUrl('typologies',requestId),{method:'POST',body:JSON.stringify(payload)});
    $('#typologyModal').close();
    event.target.reset();
    toast('Tipologia aggiunta');
    await detail(requestId);
  }catch(error){toast('Errore: '+error.message);}
}

async function deleteTypology(typologyId){
  const id=positiveId(typologyId);
  const requestId=positiveId(current?.id);
  if(id===null||requestId===null)return;
  try{
    await req(childItemUrl('typologies',id),{method:'DELETE'});
    toast('Tipologia rimossa');
    await detail(requestId);
  }catch(error){toast('Errore: '+error.message);}
}

async function addFeature(event){
  event.preventDefault();
  const requestId=positiveId(current?.id);
  if(requestId===null)return;
  try{
    const payload=buildFeaturePayload(formValues(event.target));
    await req(childCollectionUrl('features',requestId),{method:'POST',body:JSON.stringify(payload)});
    $('#featureModal').close();
    event.target.reset();
    updateFeatureFields();
    toast('Caratteristica aggiunta');
    await detail(requestId);
  }catch(error){toast('Errore: '+error.message);}
}

async function deleteFeature(featureId){
  const id=positiveId(featureId);
  const requestId=positiveId(current?.id);
  if(id===null||requestId===null)return;
  try{
    await req(childItemUrl('features',id),{method:'DELETE'});
    toast('Caratteristica rimossa');
    await detail(requestId);
  }catch(error){toast('Errore: '+error.message);}
}

function updateFeatureFields(){
  const valueType=$('#featureForm [name=value_type]').value;
  const booleanFields=$('#feature-boolean-fields');
  const numberFields=$('#feature-number-fields');
  const textFields=$('#feature-text-fields');
  booleanFields.hidden=valueType!=='boolean';
  numberFields.hidden=valueType!=='number'&&valueType!=='range';
  textFields.hidden=valueType!=='text';
  booleanFields.querySelectorAll('input,select').forEach(input=>{input.disabled=booleanFields.hidden;});
  numberFields.querySelectorAll('input,select').forEach(input=>{input.disabled=numberFields.hidden;});
  textFields.querySelectorAll('input,select').forEach(input=>{input.disabled=textFields.hidden;});
}

function updateActionScheduleField(){
  const form=$('#actionForm');
  const field=$('#actionScheduledAtField');
  const input=form.querySelector('[name=scheduled_at]');
  const scheduled=form.querySelector('[name=action]').value==='visit_scheduled';
  field.hidden=!scheduled;
  field.style.display=scheduled?'block':'none';
  input.disabled=!scheduled;
  input.required=scheduled;
  if(!scheduled)input.value='';
}

function bindUi(){
  $('#actionCancel').onclick=()=>$('#actionModal').close();
  $('#proposalCancel').onclick=()=>$('#proposalModal').close();
  $('#taskCancel').onclick=()=>$('#taskModal').close();
  $('#cancel').onclick=()=>$('#modal').close();
  $('#editCancel').onclick=()=>$('#editModal').close();
  $('#locationCancel').onclick=()=>$('#locationModal').close();
  $('#typologyCancel').onclick=()=>$('#typologyModal').close();
  $('#featureCancel').onclick=()=>$('#featureModal').close();
  $('#newBtn').onclick=()=>$('#modal').showModal();
  $('#add-location-btn').onclick=openLocation;
  $('#add-typology-btn').onclick=openTypology;
  $('#add-feature-btn').onclick=openFeature;
  $('#reload').onclick=()=>{load();dashboard();};
  $('#search').oninput=()=>load();
  $('#status').onchange=()=>load();
  $('#priority').onchange=()=>load();
  $('#featureForm [name=value_type]').onchange=updateFeatureFields;
  $('#actionForm [name=action]').onchange=updateActionScheduleField;

  $('#form').onsubmit=async event=>{
    event.preventDefault();
    try{
      const payload=buildBuyRequestPayload(formValues(event.target),true);
      const created=await req(api+'/requests',{method:'POST',body:JSON.stringify(payload)});
      const requestId=positiveId(created?.id);
      if(requestId===null)throw new Error('ID richiesta non valido');
      $('#modal').close();
      event.target.reset();
      toast('Richiesta creata');
      await dashboard();
      await load();
      await detail(requestId);
    }catch(error){toast('Errore: '+error.message);}
  };

  $('#editForm').onsubmit=async event=>{
    event.preventDefault();
    const requestId=positiveId(current?.id);
    if(requestId===null)return;
    try{
      const payload=buildBuyRequestPayload(formValues(event.target),false);
      await req(api+'/requests/'+requestId,{method:'PATCH',body:JSON.stringify(payload)});
      $('#editModal').close();
      toast('Criteri aggiornati');
      await dashboard();
      await load();
      await detail(requestId);
    }catch(error){toast('Errore: '+error.message);}
  };

  $('#locationForm').onsubmit=addLocation;
  $('#typologyForm').onsubmit=addTypology;
  $('#featureForm').onsubmit=addFeature;

  $('#actionForm').onsubmit=async event=>{
    event.preventDefault();
    if(actionSubmitPending)return;
    const requestId=positiveId(current?.id);
    const values=formValues(event.target);
    const matchId=positiveId(values.match_id);
    if(requestId===null||matchId===null)return;
    const submit=$('#actionSubmit');
    actionSubmitPending=true;
    submit.disabled=true;
    try{
      const body=buildMatchDecisionPayload(values);
      await req(`${api}/requests/${requestId}/matches/${matchId}/decision`,{method:'POST',body:JSON.stringify(body)});
      $('#actionModal').close();
      event.target.reset();
      updateActionScheduleField();
      toast('Esito registrato');
      await detail(requestId);
      await dashboard();
    }catch(error){toast('Errore: '+error.message);}
    finally{
      actionSubmitPending=false;
      submit.disabled=false;
    }
  };

  $('#proposalForm').onsubmit=async event=>{
    event.preventDefault();
    if(proposalSubmitPending)return;
    const requestId=positiveId(current?.id);
    const values=formValues(event.target);
    const proposalId=values.proposal_id?positiveId(values.proposal_id):null;
    if(requestId===null||values.proposal_id&&proposalId===null)return;
    const submit=$('#proposalSubmit');
    proposalSubmitPending=true;
    submit.disabled=true;
    try{
      const body=proposalId===null?buildProposalCreatePayload(values):buildProposalUpdatePayload(values);
      await req(proposalId===null?'/api/proposals':`/api/proposals/${proposalId}`,{method:proposalId===null?'POST':'PATCH',body:JSON.stringify(body)});
      $('#proposalModal').close();
      event.target.reset();
      toast(proposalId===null?'Proposta creata':'Proposta aggiornata');
      await detail(requestId);
    }catch(error){toast('Errore: '+error.message);}
    finally{
      proposalSubmitPending=false;
      submit.disabled=false;
    }
  };

  $('#taskForm').onsubmit=async event=>{
    event.preventDefault();
    const requestId=positiveId(current?.id);
    if(requestId===null)return;
    const body=Object.fromEntries(Object.entries(formValues(event.target)).filter(([,value])=>value!==''));
    if(body.due_at)body.due_at=new Date(body.due_at).toISOString();
    await req(`${api}/requests/${requestId}/tasks`,{method:'POST',body:JSON.stringify(body)});
    $('#taskModal').close();
    event.target.reset();
    toast('Task CORE creato');
    await detail(requestId);
    await dashboard();
  };

  $('#login-form').addEventListener('submit',login);
  $('#logout-btn').addEventListener('click',()=>logout('Sessione amministrativa chiusa.'));
  updateFeatureFields();
  updateActionScheduleField();
  showLogin();
}

if(typeof document!=='undefined')bindUi();

if(typeof window!=='undefined'){
  window.openProposal=openProposal;
  window.transitionProposal=transitionProposal;
}

if(typeof module!=='undefined'&&module.exports){
  module.exports={positiveId,buildBuyRequestPayload,buildLocationPayload,buildTypologyPayload,buildFeaturePayload,buildMatchDecisionPayload,buildProposalCreatePayload,buildProposalUpdatePayload,proposalActions,childCollectionUrl,childItemUrl};
}
