"""Domain exceptions translated to HTTP responses by the router."""


class CoreError(Exception):
    """Base error for the CORE module."""


class NotFoundError(CoreError):
    pass


class ConflictError(CoreError):
    pass


class PropertyInTrash(ConflictError):
    """DELETE-ARCH Fase 2B2: nuovo collegamento verso un immobile nel Cestino
    (409 PROPERTY_IN_TRASH). Vedi core/property_trash.py."""
    code = "PROPERTY_IN_TRASH"

    def __init__(self, message: str = "PROPERTY_IN_TRASH: l'immobile è nel Cestino e non accetta nuovi collegamenti"):
        super().__init__(message)
        self.extra = {}


class ContactInTrash(ConflictError):
    """CESTINO-CONTATTI-1: nuovo collegamento o modifica verso un contatto nel
    Cestino (409 CONTACT_IN_TRASH). Vedi core/contact_trash.py."""
    code = "CONTACT_IN_TRASH"

    def __init__(self, message: str = "CONTACT_IN_TRASH: il contatto è nel Cestino: ripristinalo prima di usarlo"):
        super().__init__(message)
        self.extra = {}


class BuildingInTrash(ConflictError):
    """CESTINO-EDIFICI-1: nuova unita' o modifica verso un edificio nel Cestino
    (409 BUILDING_IN_TRASH). Vedi property/building_lifecycle.py."""
    code = "BUILDING_IN_TRASH"

    def __init__(self, message: str = "L'edificio è nel Cestino: ripristinalo prima di usarlo"):
        super().__init__(message)
        self.extra = {}


class ValidationError(CoreError):
    pass


class PermissionDenied(CoreError):
    """The caller's role does not permit this operation.

    Distinct from NotFoundError: nothing is being hidden. Under design spec
    D-6, a caller who legitimately knows an endpoint exists and is refused on
    role grounds gets 403, while a record belonging to another agency gets 404
    - so this is never raised for a record the caller cannot see.

    The role decision itself is not made here. It belongs to
    operator_auth.permissions, which is the single home of the agency role
    matrix; this type only carries the outcome to the router.
    """
