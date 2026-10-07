-- PERTINENZE-1 (FASE E): la natura di pertinenza di una scheda immobile,
-- indipendente dal collegamento.
--
-- Additiva. Due colonne su `properties`; NESSUN backfill: nessuna riga
-- esistente cambia valore (il default e' neutro).
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- Perche'. `parent_property_id` (083) e' la relazione CORRENTE con l'unita'
-- principale: dice «collegata a IMM-x», non «e' una pertinenza». Senza un
-- dato proprio, una pertinenza autonoma (con subalterno proprio) censita ma
-- non ancora collegata - o scollegata - contava come unita' principale, e un
-- posto auto diventato unita' perdeva la distinzione dal garage (la tipologia
-- `garage` li accomuna).
--
--   is_pertinenza    la scheda e' una pertinenza autonoma (unita' catastale
--                    separata al servizio di un'altra), collegata o no. Una
--                    scheda con `parent_property_id` e' una pertinenza anche
--                    con FALSE (righe precedenti alla 089): la regola e'
--                    `parent_property_id IS NOT NULL OR is_pertinenza`.
--                    Lo scollegamento NON la riporta a FALSE: resta una
--                    pertinenza «da collegare».
--   pertinenza_kind  che cos'e', con lo STESSO catalogo degli accessori
--                    (`property_accessories.kind`, 087): box e posto auto
--                    distinti. NULL = non indicato (mai «altro» d'ufficio).
--                    Solo su una pertinenza.
--
-- Il collegamento all'EDIFICIO (`building_id`) resta indipendente: una
-- pertinenza puo' stare nella palazzina senza essere ancora associata a un
-- appartamento, o essere associata a un'unita' di un altro edificio.

ALTER TABLE properties
  ADD COLUMN IF NOT EXISTS is_pertinenza BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS pertinenza_kind VARCHAR(30);

ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_pertinenza_kind_chk;
ALTER TABLE properties ADD CONSTRAINT properties_pertinenza_kind_chk CHECK (
    pertinenza_kind IS NULL OR (
        is_pertinenza
        AND pertinenza_kind IN ('cantina', 'soffitta', 'posto_auto', 'giardino', 'terrazzo', 'box', 'deposito',
                                'altro', 'taverna', 'balcone', 'piscina', 'posto_moto', 'posto_bici')));

-- Le pertinenze di una palazzina (collegate e da collegare) si leggono per
-- edificio: indice parziale, piccolo.
CREATE INDEX IF NOT EXISTS idx_properties_building_pertinenze
    ON properties (building_id) WHERE is_pertinenza;
