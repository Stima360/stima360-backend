STIMA360 PROPERTY 0.1

Additive module. It does not alter CORE tables, legacy endpoints, valuation logic, PDF or production flows.

Test deployment order:
1. Apply migrations/002_property_01.sql only to stima360-db-test.
2. Deploy the branch core-0.1-test to stima360-backend-test.
3. Open /property-admin/.

Rollback: migrations/002_property_01_down.sql (test environment only).
Documents and photos are metadata/URL records in 0.1; no binary storage migration is performed.
