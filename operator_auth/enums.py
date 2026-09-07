"""Constants for operator authentication.

Values fixed by the approved P26-1 design spec, section 7.1.
"""

# Distinct from the owner portal's 'stima360_owner_session'. Two different
# principals authenticate against this application; a shared cookie name would
# let one overwrite the other's session.
COOKIE_NAME = "stima360_operator_session"

# Absolute session lifetime. Twelve hours forces a daily re-login and matches
# the owner portal, so neither principal outlives a working day.
SESSION_MAX_HOURS = 12

# Idle window. Wider than the owner portal's 60 minutes because an operator
# works inside the OS Shell all day, whereas an owner opens the portal briefly.
# Four hours survives a morning and a lunch break without a re-login.
SESSION_IDLE_MINUTES = 240

# PBKDF2-HMAC-SHA256 cost. Encoded into every stored hash, so this can be
# raised later without a migration: an old hash keeps verifying at its own
# recorded cost.
PBKDF2_ITERATIONS = 600_000

# The three agency roles. platform_admin is deliberately absent: it is not an
# agency role but a flag on operator_users, and a platform admin holds no
# agency_memberships row at all. See the design spec section 3.2.
AGENCY_ROLES = (
    "agency_owner",
    "agency_admin",
    "agent",
)

# The Default Agency, resolved by slug rather than by id. Every legacy record
# is backfilled to it and every public estimation routes to it.
DEFAULT_AGENCY_SLUG = "stima360"
