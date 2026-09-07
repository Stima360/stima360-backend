#!/usr/bin/env python3
"""P26-1 Task 17 - seed Agency B and the six TEST operators.

TEST only, and never part of the migration ledger. Seeding is data, not schema:
putting it in `migrations/` would make a set of test accounts a permanent,
forward-only fact about every environment the ledger is replayed into,
including production. It lives in `scripts/` and is run by hand.

    python scripts/p26_seed_agencies_test.py --operator "Nome Cognome"

Refuses to do anything unless:

* `DB_NAME` identifies a TEST database - the same guard the migration runner
  uses, imported rather than reimplemented, so there is one definition of what
  "TEST" means; and
* all six passwords are present in the environment. No default, no fallback,
  no literal in this file. A missing variable stops the run.

Six operators and five memberships. `platform_admin` deliberately gets no
membership at all: an unbound operator with `is_platform_admin = TRUE` is what
a platform admin *is* (design spec section 3.2). Giving it a membership would
bind it to one agency and quietly turn the system's only cross-agency identity
into an ordinary owner.

Idempotent. Every insert is guarded by `WHERE NOT EXISTS`, so a second run adds
nothing and the script can be re-run after a partial failure.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operator_auth.enums import DEFAULT_AGENCY_SLUG  # noqa: E402
from operator_auth.security import hash_password  # noqa: E402
from scripts.p26_migrate import (  # noqa: E402
    GuardFailure,
    assert_operator_identity,
    assert_test_database_name,
)

AGENCY_B_SLUG = "agenzia-b-test"
AGENCY_B_NAME = "Agenzia B (TEST)"

# Stated explicitly so a later edit cannot drift the shape without failing
# tests/test_p26_1_seed_and_fixtures.py.
EXPECTED_OPERATOR_COUNT = 6
EXPECTED_MEMBERSHIP_COUNT = 5

# (key, email, password environment variable, is_platform_admin)
OPERATORS = (
    ("owner_a", "owner.a@test.stima360.local", "P26_SEED_OWNER_A_PASSWORD", False),
    ("admin_a", "admin.a@test.stima360.local", "P26_SEED_ADMIN_A_PASSWORD", False),
    ("agent_a", "agent.a@test.stima360.local", "P26_SEED_AGENT_A_PASSWORD", False),
    ("agent_a2", "agent.a2@test.stima360.local", "P26_SEED_AGENT_A2_PASSWORD", False),
    ("owner_b", "owner.b@test.stima360.local", "P26_SEED_OWNER_B_PASSWORD", False),
    ("platform_admin", "platform@test.stima360.local", "P26_SEED_PLATFORM_PASSWORD", True),
)

# (operator key, agency slug, role). Five rows for six operators: the platform
# admin is absent by design.
MEMBERSHIPS = (
    ("owner_a", DEFAULT_AGENCY_SLUG, "agency_owner"),
    ("admin_a", DEFAULT_AGENCY_SLUG, "agency_admin"),
    ("agent_a", DEFAULT_AGENCY_SLUG, "agent"),
    ("agent_a2", DEFAULT_AGENCY_SLUG, "agent"),
    ("owner_b", AGENCY_B_SLUG, "agency_owner"),
)


def read_passwords() -> dict[str, str]:
    """Collect the six passwords, or refuse.

    Reported together rather than one at a time: an operator setting up a TEST
    environment should learn everything that is missing in one go.
    """
    values: dict[str, str] = {}
    missing: list[str] = []
    for _, _, variable, _ in OPERATORS:
        value = os.getenv(variable)
        if not value:
            missing.append(variable)
        else:
            values[variable] = value
    if missing:
        raise GuardFailure(
            "BLOCKED: these environment variables are unset: " + ", ".join(missing)
        )
    return values


def seed(cur, operator_identity: str, passwords: dict[str, str]) -> dict[str, int]:
    """Insert the agency, the operators and the memberships. Idempotent."""
    counts = {"agencies": 0, "operators": 0, "memberships": 0}

    cur.execute(
        """
        INSERT INTO agencies (slug, name, status)
        SELECT %s, %s, 'active'
         WHERE NOT EXISTS (SELECT 1 FROM agencies WHERE slug = %s)
        """,
        (AGENCY_B_SLUG, AGENCY_B_NAME, AGENCY_B_SLUG),
    )
    counts["agencies"] += cur.rowcount

    for key, email, variable, is_platform_admin in OPERATORS:
        normalized = email.strip().lower()
        cur.execute(
            """
            INSERT INTO operator_users (
                email, email_normalized, password_hash, status, is_platform_admin
            )
            SELECT %s, %s, %s, 'active', %s
             WHERE NOT EXISTS (
                 SELECT 1 FROM operator_users WHERE email_normalized = %s
             )
            """,
            (
                email,
                normalized,
                hash_password(passwords[variable]),
                is_platform_admin,
                normalized,
            ),
        )
        counts["operators"] += cur.rowcount

    for key, agency_slug, role in MEMBERSHIPS:
        email = next(e for k, e, _, _ in OPERATORS if k == key)
        normalized = email.strip().lower()
        cur.execute(
            """
            INSERT INTO agency_memberships (agency_id, operator_user_id, role, status)
            SELECT a.id, u.id, %s, 'active'
              FROM agencies a
              JOIN operator_users u ON u.email_normalized = %s
             WHERE a.slug = %s
               AND NOT EXISTS (
                   SELECT 1 FROM agency_memberships m
                    WHERE m.agency_id = a.id AND m.operator_user_id = u.id
               )
            """,
            (role, normalized, agency_slug),
        )
        counts["memberships"] += cur.rowcount

    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed P26-1 TEST agencies and operators.")
    parser.add_argument("--operator", required=True, help="who is running this seed")
    arguments = parser.parse_args(argv)

    try:
        identity = assert_operator_identity(arguments.operator)
        database_name = assert_test_database_name(os.getenv("DB_NAME"))
        passwords = read_passwords()
    except GuardFailure as failure:
        print(str(failure), file=sys.stderr)
        return 2

    from database import get_connection

    connection = get_connection()
    try:
        with connection.cursor() as cur:
            counts = seed(cur, identity, passwords)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    print(
        f"seeded {database_name}: agencies +{counts['agencies']}, "
        f"operators +{counts['operators']}, memberships +{counts['memberships']} "
        f"(operator: {identity})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
