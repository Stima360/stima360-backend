-- IMPORTAZIONE RUBRICA / SELEZIONE MULTIPLA - la guardia T6 dei contatti senza
-- scorrere tutti i contatti.
--
-- `communication_contact_stop_fence()` (097, Tappa 6) blocca FOR UPDATE, in
-- ordine (id, agency_id), ogni contatto che la riga scritta puo' coinvolgere:
-- per id diretto, per lead, per stima collegata al lead, per immobile. La 097
-- trova questi contatti con UNA select su `contacts` le cui condizioni sono
-- unite da OR con sotto-query EXISTS: PostgreSQL non puo' usare gli indici e
-- valuta le sotto-query per OGNI riga di `contacts`. Il costo cresce con la
-- rubrica (circa 40 ms a scrittura con 5.500 contatti) e si paga su nove
-- tabelle, comprese la pausa delle automazioni e la cronologia scritte da ogni
-- spostamento nel Cestino.
--
-- Qui l'INSIEME e' lo stesso, condizione per condizione, ma calcolato come
-- UNIONE di ricerche dirette sugli indici che esistono gia' (chiavi primarie,
-- lead_stime(stima_id), property_contacts(property_id)):
--   A  contatto per id (contact_id / owner_contact_id) nell'agenzia della riga;
--   B1 contatto del lead nominato (lead_id);
--   B2 contatto del lead collegato alla stima nominata (stima_id, stima_id_snapshot);
--   C  contatto per id (contact_id) se l'immobile nominato e' della sua agenzia;
--   D  proprietario/venditore dell'immobile nominato, nella sua agenzia.
-- Stessi valori NEW e OLD, stessi cast, stesso ordine dei lock, stesso
-- FOR UPDATE: nessuna protezione cambia. Le nove associazioni dei trigger della
-- 097 restano quelle che sono (la funzione si sostituisce, non si ricollega).
--
-- `communication_contact_stop_fence_targets(data, old_data)` e' l'insieme, in
-- una funzione sola, cosi' le prove lo confrontano con la regola della 097.
-- Nessun dato toccato. La transazione e' del runner.

CREATE FUNCTION communication_contact_stop_fence_targets(data JSONB, old_data JSONB)
RETURNS TABLE (id BIGINT, agency_id BIGINT) AS $fn$
  WITH v AS (
    SELECT ARRAY[(data->>'contact_id')::bigint, (old_data->>'contact_id')::bigint,
                 (data->>'owner_contact_id')::bigint, (old_data->>'owner_contact_id')::bigint] AS contatti_e_referenti,
           ARRAY[(data->>'contact_id')::bigint, (old_data->>'contact_id')::bigint] AS contatti,
           ARRAY[(data->>'agency_id')::bigint, (old_data->>'agency_id')::bigint] AS agenzie,
           ARRAY[(data->>'lead_id')::bigint, (old_data->>'lead_id')::bigint] AS leads,
           ARRAY[(data->>'stima_id')::integer, (old_data->>'stima_id')::integer,
                 (data->>'stima_id_snapshot')::integer, (old_data->>'stima_id_snapshot')::integer] AS stime,
           ARRAY[(data->>'property_id')::bigint, (old_data->>'property_id')::bigint] AS immobili
  )
  SELECT DISTINCT t.id, t.agency_id FROM (
    -- A
    SELECT c.id, c.agency_id FROM v, contacts c
     WHERE c.id = ANY(v.contatti_e_referenti) AND c.agency_id = ANY(v.agenzie)
    UNION
    -- B1
    SELECT c.id, c.agency_id FROM v, leads l JOIN contacts c ON c.id = l.contact_id AND c.agency_id = l.agency_id
     WHERE l.id = ANY(v.leads)
    UNION
    -- B2
    SELECT c.id, c.agency_id FROM v, lead_stime ls JOIN leads l ON l.id = ls.lead_id
      JOIN contacts c ON c.id = l.contact_id AND c.agency_id = l.agency_id
     WHERE ls.stima_id = ANY(v.stime)
    UNION
    -- C
    SELECT c.id, c.agency_id FROM v, contacts c JOIN properties p ON p.agency_id = c.agency_id
     WHERE c.id = ANY(v.contatti) AND p.id = ANY(v.immobili)
    UNION
    -- D
    SELECT c.id, c.agency_id FROM v, property_contacts pc JOIN properties p ON p.id = pc.property_id
      JOIN contacts c ON c.id = pc.contact_id AND c.agency_id = p.agency_id
     WHERE pc.role IN ('owner', 'seller') AND pc.property_id = ANY(v.immobili)
  ) t
$fn$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION communication_contact_stop_fence() RETURNS trigger AS $fn$
DECLARE data JSONB; old_data JSONB; cid BIGINT; agency BIGINT;
BEGIN
 data := to_jsonb(NEW);
 old_data := CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 FOR cid,agency IN
   SELECT t.id, t.agency_id FROM communication_contact_stop_fence_targets(data, old_data) t
   ORDER BY t.id, t.agency_id
 LOOP
   PERFORM 1 FROM contacts c WHERE c.id=cid AND c.agency_id=agency FOR UPDATE;
 END LOOP;
 RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
