"""STIMA360 operator authentication.

Additive module for P26-1. It provides the real operator identity that
replaces, over time, the shared ADMIN_USER/ADMIN_PASS credential: agency
operators, server-side sessions, and the agency scope derived from them.

Task 3 delivers the pure security foundation only - constants and stateless
cryptographic helpers. There is no database access, no repository, no service
and no router in this package yet.

This package deliberately does not import from owner/. The two modules solve a
similar problem for two different principals (the operator here, the property
owner there), and the small duplication in security.py is preferred to a
cross-module dependency.
"""
