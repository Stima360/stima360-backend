from . import repository


def _dump(model, *, exclude_unset=False):
    return model.dict(exclude_unset=exclude_unset)


def create_sale(model, created_by):
    return repository.create_sale(_dump(model), created_by)


def get_sale(sale_id):
    return repository.get_sale(sale_id)


def list_sales(**filters):
    return repository.list_sales(**filters)


def update_sale(sale_id, model):
    return repository.update_sale(sale_id, _dump(model, exclude_unset=True))


def complete_sale(sale_id, actor):
    return repository.complete_sale(sale_id, actor)


def cancel_sale(sale_id, actor):
    return repository.cancel_sale(sale_id, actor)


# P26-5 scoped surface. The six HTTP handlers call these; the legacy functions
# above keep their signatures for tests/test_sale_01_p10.py.

def create_sale_scoped(ctx, model, created_by):
    return repository.create_sale_scoped(ctx, _dump(model), created_by)


def get_sale_scoped(ctx, sale_id):
    return repository.get_sale_scoped(ctx, sale_id)


def list_sales_scoped(ctx, **filters):
    return repository.list_sales_scoped(ctx, **filters)


def update_sale_scoped(ctx, sale_id, model):
    return repository.update_sale_scoped(ctx, sale_id, _dump(model, exclude_unset=True))


def complete_sale_scoped(ctx, sale_id, actor):
    return repository.complete_sale_scoped(ctx, sale_id, actor)


def cancel_sale_scoped(ctx, sale_id, actor):
    return repository.cancel_sale_scoped(ctx, sale_id, actor)
