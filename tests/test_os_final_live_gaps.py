"""Regression coverage for the five P25 live certification gaps."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'static/os_shell/assets'


def run_js(view, expression):
    source = (ASSETS / 'components/st-table.js').read_text() + '\n' + (ASSETS / view).read_text()
    script = "const vm=require('node:vm');const assert=require('node:assert/strict');\n"
    script += 'let source=' + json.dumps(source) + ';\n'
    script += "source=source.replace(/^import [\\s\\S]*?;$/mg,'').replace(/export /g,'');\n"
    script += "vm.runInNewContext(source + '\\n' + " + json.dumps(expression) + ", {assert, console, Date, Set, Map, process});"
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_contact_relationship_links_use_os_entity_ids():
    run_js('views/contatto-dettaglio.js', r'''
      assert.match(renderRichieste([{id:16,title:'Buyer'}]), /href="#\/acquirenti\/16"/);
      assert.match(renderAbbinamenti([{id:19,property_id:30}]), /href="#\/abbinamenti\/19"/);
      assert.match(renderVisite([{id:17,property_id:30}]), /href="#\/immobili\/30\/visite\/17"/);
      assert.match(renderImmobili({properties:[{id:30,title:'<Casa>'}],contactId:5,failedCount:0}), /href="#\/immobili\/30"/);
    ''')


def test_property_owner_and_lead_links():
    run_js('views/immobile-dettaglio.js', r'''
      assert.match(renderProprietari([{contact_id:55}], null), /href="#\/contatti\/55"/);
      assert.match(renderLeadLinks([{lead_id:34,contact_id:55}]), /href="#\/contatti\/55\/lead\/34"/);
    ''')


def test_detail_tabs_and_match_proposal_destination():
    for name in ('contatto', 'immobile'):
        source = (ASSETS / f'views/{name}-dettaglio.js').read_text()
        assert 'params[1]' in source
        assert 'params[2]' in source
    source = (ASSETS / 'views/abbinamento-dettaglio.js').read_text()
    assert '#/immobili/${escapeHtml(match.property_id)}/proposte' in source


def test_proposal_opens_linked_sale_in_both_views():
    for view in ('immobile', 'acquirente'):
        run_js(f'views/{view}-dettaglio.js', r'''
          const html = renderVenditaCell({id:9,status:'accepted'}, {id:42,status:'completed'}, new Set(), false);
          assert.match(html, /sale-detail-btn/);
          assert.match(html, /data-sale-id="42"/);
          assert.match(html, /Completata/);
        ''')


def test_os_search_reuses_shared_engine_and_exposes_results():
    component = ASSETS / 'components/global-search.js'
    assert component.exists()
    source = component.read_text()
    assert 'searchGlobal' in source
    assert 'textContent' in source
    assert "mountGlobalSearch" in (ASSETS / 'main.js').read_text()


def test_shared_search_queries_and_failure_isolation():
    run_js('core/global-search.js', r'''
      (async () => {
        const calls=[];
        const api=async path => {
          calls.push(path);
          if(path.includes('/contacts?')) return {items:[{id:5,display_name:'A&B'}]};
          if(path.includes('/leads?')) return {items:[{id:34,contact_id:5}]};
          if(path.includes('/properties?')) throw new Error('unavailable');
          return {items:[]};
        };
        const result=await searchGlobal('A&B',api,{includeLeads:true});
        assert(calls.some(p=>p.includes('search=A%26B')));
        assert(result.items.some(i=>i.type==='contact' && i.id===5));
        assert(result.items.some(i=>i.type==='lead' && i.id===34 && i.contact_id===5));
        assert.equal(result.failed,1);
        const empty=await searchGlobal('zz',async()=>({items:[]}));
        assert.equal(empty.items.length,0);
      })().catch(e=>{console.error(e);process.exitCode=1;});
    ''')


def test_unknown_route_is_explicit_and_valid_details_keep_params():
    run_js('core/router.js', r'''
      (async()=>{
        globalThis.window={location:{hash:'#/residual-route-not-found'},addEventListener(){}};
        const output={innerHTML:'',textContent:''};
        let visits=0;
        registerRoute('oggi',()=>{visits++;});
        registerRoute('contatti',(container,params)=>{assert.equal(params[0],'55');visits++;});
        initRouter(output);
        await renderCurrentRoute();
        assert.equal(visits,0);
        assert.match(output.textContent,/non trovata/i);
        window.location.hash='#/contatti/55';
        await renderCurrentRoute();
        assert.equal(visits,1);
        assert.equal(currentRouteName(),'contatti');
        window.location.hash='';
        await renderCurrentRoute();
        assert.equal(visits,2);
      })().catch(e=>{console.error(e);process.exitCode=1;});
    ''')


def test_visit_context_submit_sends_exact_selected_visit_for_every_outcome():
    source = (ASSETS / 'views/acquirente-dettaglio.js').read_text()
    dialog_fn = source[source.index('  function openMatchDecisionDialog('):source.index('  // --- P25.5: Criteri')]
    run_js('views/acquirente-dettaglio.js', dialog_fn + r'''
      (async()=>{
        for(const action of ['visited','interested','discarded','offer_candidate']) {
          for(const id of [101,102]) {
            const elements=new Map();
            let submit;
            const dialog={innerHTML:'',showModal(){},close(){},querySelector(selector){
              if(!elements.has(selector)) elements.set(selector,{value:action,hidden:false,addEventListener(type,fn){if(type==='submit')submit=fn;}});
              return elements.get(selector);
            }};
            globalThis.container={querySelector:()=>dialog};
            globalThis.data={interactions:[101,102].map(id=>({interaction_type:'visit_scheduled',match_id:11,property_visit_id:id}))};
            globalThis.requestId=7;
            globalThis.FormData=class {get(key){return {action,property_visit_id:String(id),reason_code:'buyer_decision'}[key]||'';}};
            const calls=[];
            globalThis.apiPost=async (path,payload)=>calls.push({path,payload});
            globalThis.reloadRequest=async()=>{};
            globalThis.showTab=()=>{};
            globalThis.contentEl={querySelector:()=>null};
            openMatchDecisionDialog({id:11,property_id:21},String(id));
            assert(dialog.innerHTML.includes(`value="${id}"`));
            assert(!dialog.innerHTML.includes(`value="${id===101?102:101}"`));
            await submit({preventDefault(){},target:{}});
            assert.equal(calls.length,1);
            assert.equal(calls[0].path,'/api/buy/requests/7/matches/11/decision');
            assert.equal(calls[0].payload.property_visit_id,id);
            assert.equal(calls[0].payload.action,action);
          }
        }
      })().catch(e=>{console.error(e);process.exitCode=1;});
    ''')


def test_search_result_routes_validate_ids_and_preserve_lead_context():
    run_js('components/global-search.js', r'''
      assert.equal(searchResultHref({type:'lead',id:34,contact_id:55}),'#/contatti/55/lead/34');
      assert.equal(searchResultHref({type:'contact',id:55}),'#/contatti/55');
      assert.equal(searchResultHref({type:'buy',id:16}),'#/acquirenti/16');
      assert.equal(searchResultHref({type:'property',id:30}),'#/immobili/30');
      assert.equal(searchResultHref({type:'match',id:19}),'#/abbinamenti/19');
      assert.equal(searchResultHref({type:'contact',id:'<script>'}),null);
      assert.equal(searchResultHref({type:'lead',id:34,contact_id:null}),null);
    ''')


def test_numeric_search_missing_match_is_not_server_failure():
    run_js('core/global-search.js', r'''
      (async()=>{
        const result=await searchGlobal('19',async path=>{
          if(path==='/api/match/matches/19') {const error=new Error('not found');error.status=404;throw error;}
          return {items:[]};
        });
        assert.equal(result.failed,0);
        assert.equal(result.items.length,0);
        assert.equal(result.unavailable,false);
      })().catch(e=>{console.error(e);process.exitCode=1;});
    ''')
