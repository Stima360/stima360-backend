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
