function positiveId(value) { const n=Number(value); return Number.isSafeInteger(n)&&n>0?n:null; }
async function lookupGlobalSearchMatch(id, api){
 try{
   return await api(`/api/match/matches/${id}`);
 }catch(e){
   if(e.status===404)return null;
   throw e;
 }
}

function normalizeGlobalSearchResults(type,items){
 return (items||[]).map(item=>{
   if(type==='contact')return {
     type,
     id:item.id,
     typeLabel:'CONTATTO',
     title:item.display_name||item.company_name||[item.first_name,item.last_name].filter(Boolean).join(' ')||`Contatto #${item.id}`,
     subtitle:[item.email,item.phone].filter(Boolean).join(' · '),
     status:item.status||''
   };

   if(type==='property')return {
     type,
     id:item.id,
     typeLabel:'IMMOBILE',
     title:item.title||`Immobile #${item.id}`,
     subtitle:[item.code,item.address,item.city].filter(Boolean).join(' · '),
     status:item.commercial_status||''
   };

   if(type==='buy')return {
     type,
     id:item.id,
     typeLabel:'BUY',
     title:item.title||`Richiesta #${item.id}`,
     subtitle:[item.contact_name,item.contact_phone].filter(Boolean).join(' · '),
     status:item.status||''
   };

   if(type==='match')return {
     type,
     id:item.id,
     typeLabel:'MATCH',
     title:`Match #${item.id}`,
     subtitle:[item.buy_title,item.property_title].filter(Boolean).join(' ↔ '),
     status:item.match_class||item.commercial_status||''
   };

   return null;
 }).filter(Boolean);
}

export async function searchGlobal(query, api, { includeLeads = false } = {}) {
 const encoded=encodeURIComponent(query);

 const requests=[
   api(`/api/core/contacts?search=${encoded}&limit=5`)
     .then(data=>({type:'contact',items:data.items||[]})),
   api(`/api/property/properties?search=${encoded}&limit=5`)
     .then(data=>({type:'property',items:data.items||[]})),
   api(`/api/buy/requests?search=${encoded}&limit=5`)
     .then(data=>({type:'buy',items:data.items||[]}))
 ];

 const matchId=positiveId(query);

 if(matchId!==null){
   requests.push(
     lookupGlobalSearchMatch(matchId, api)
       .then(item=>({type:'match',items:item?[item]:[]}))
   );
 }

 const settled=await Promise.allSettled(requests);


 const items=[];
 let failed=0;
 for(const result of settled){
   if(result.status==='fulfilled') items.push(...normalizeGlobalSearchResults(result.value.type,result.value.items));
   else failed+=1;
 }
 // CORE has no free-text lead endpoint: resolve leads through matching contacts.
 if(includeLeads){
   const contacts=items.filter(item=>item.type==='contact');
   const leads=await Promise.allSettled(contacts.map(contact=>api(`/api/core/leads?contact_id=${contact.id}&limit=5`)));
   for(const result of leads){
     if(result.status==='fulfilled') for(const lead of result.value.items||[]) items.push({type:'lead',id:lead.id,contact_id:lead.contact_id,typeLabel:'LEAD',title:`Lead #${lead.id}`,subtitle:[lead.pipeline,lead.stage].filter(Boolean).join(' · '),status:lead.status});
     else failed+=1;
   }
 }
 return {items,failed,unavailable:failed===settled.length && !items.length};
}
