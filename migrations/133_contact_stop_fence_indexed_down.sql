-- Down per 133: la guardia T6 torna IDENTICA alla 097 (lo stesso testo), poi
-- la funzione dell'insieme si elimina. I trigger restano associati alla stessa
-- funzione. Nessun dato toccato. La transazione e' del runner.

CREATE OR REPLACE FUNCTION communication_contact_stop_fence() RETURNS trigger AS $fn$
DECLARE data JSONB; old_data JSONB; cid BIGINT; agency BIGINT;
BEGIN
 data := to_jsonb(NEW);
 old_data := CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 FOR cid,agency IN
   SELECT DISTINCT c.id,c.agency_id FROM contacts c WHERE
     (c.id IN ((data->>'contact_id')::bigint,(old_data->>'contact_id')::bigint,
               (data->>'owner_contact_id')::bigint,(old_data->>'owner_contact_id')::bigint)
       AND c.agency_id IN ((data->>'agency_id')::bigint,(old_data->>'agency_id')::bigint))
     OR EXISTS(SELECT 1 FROM leads l LEFT JOIN lead_stime ls ON ls.lead_id=l.id
               WHERE l.contact_id=c.id AND l.agency_id=c.agency_id AND
               (l.id IN ((data->>'lead_id')::bigint,(old_data->>'lead_id')::bigint)
                 OR ls.stima_id IN ((data->>'stima_id')::integer,(old_data->>'stima_id')::integer,
                   (data->>'stima_id_snapshot')::integer,(old_data->>'stima_id_snapshot')::integer)))
     OR (c.id IN ((data->>'contact_id')::bigint,(old_data->>'contact_id')::bigint)
         AND EXISTS(SELECT 1 FROM properties p WHERE p.agency_id=c.agency_id
             AND p.id IN ((data->>'property_id')::bigint,(old_data->>'property_id')::bigint)))
     OR EXISTS(SELECT 1 FROM property_contacts pc JOIN properties p ON p.id=pc.property_id
               WHERE pc.contact_id=c.id AND p.agency_id=c.agency_id AND pc.role IN ('owner','seller')
                 AND pc.property_id IN ((data->>'property_id')::bigint,(old_data->>'property_id')::bigint))
   ORDER BY c.id,c.agency_id
 LOOP
   PERFORM 1 FROM contacts c WHERE c.id=cid AND c.agency_id=agency FOR UPDATE;
 END LOOP;
 RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
DROP FUNCTION IF EXISTS communication_contact_stop_fence_targets(JSONB, JSONB);
