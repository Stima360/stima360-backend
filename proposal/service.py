from . import repository


def _dump(model, *, exclude_unset=False):
    return model.dict(exclude_unset=exclude_unset)


def create_proposal(model, created_by):
    return repository.create_proposal(_dump(model), created_by)


def get_proposal(proposal_id):
    return repository.get_proposal(proposal_id)


def list_proposals(**filters):
    return repository.list_proposals(**filters)


def update_proposal(proposal_id, model, created_by):
    return repository.update_proposal(proposal_id, _dump(model, exclude_unset=True), created_by)


def transition_proposal(proposal_id, model, created_by):
    return repository.transition_proposal(proposal_id, model.target_status, created_by)


# P26-5 scoped surface. The five HTTP handlers call these; the legacy functions
# above keep their signatures for tests/test_next6_p1_proposals.py. ctx is
# threaded through unread: this layer decides what is asked, never whose it is.

def create_proposal_scoped(ctx, model, created_by):
    return repository.create_proposal_scoped(ctx, _dump(model), created_by)


def get_proposal_scoped(ctx, proposal_id):
    return repository.get_proposal_scoped(ctx, proposal_id)


def list_proposals_scoped(ctx, **filters):
    return repository.list_proposals_scoped(ctx, **filters)


def update_proposal_scoped(ctx, proposal_id, model, created_by):
    return repository.update_proposal_scoped(
        ctx, proposal_id, _dump(model, exclude_unset=True), created_by
    )


def transition_proposal_scoped(ctx, proposal_id, model, created_by):
    return repository.transition_proposal_scoped(
        ctx, proposal_id, model.target_status, created_by
    )
