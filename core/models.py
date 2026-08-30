"""CORE entity documentation.

The project currently uses psycopg2 and explicit SQL rather than an ORM. Database
models are therefore defined by migrations/001_core_contacts_leads.sql, while
Pydantic transport models live in core/schemas.py.
"""
