from . import repository

def dump(model,exclude_unset=False):
    return model.model_dump(exclude_unset=exclude_unset) if hasattr(model,'model_dump') else model.dict(exclude_unset=exclude_unset)
# P26-2C: ctx is threaded straight through, unread and unmodified. This layer
# decides what the record looks like, never which agency it belongs to.
def create_property(ctx,p):return repository.create_property(ctx,dump(p))
def list_properties(*a,**k):return repository.list_properties(*a,**k)
def get_property(ctx,i):return repository.get_property(ctx,i)
def update_property(ctx,i,p):return repository.update_property(ctx,i,dump(p,True))
def archive_property(ctx,i):return repository.archive_property(ctx,i)
def add_contact(ctx,i,p):return repository.add_contact(ctx,i,dump(p))
def delete_contact(ctx,i,c,r):return repository.delete_contact(ctx,i,c,r)
def add_lead(ctx,i,p):return repository.add_lead(ctx,i,dump(p))
def delete_lead(ctx,i,l):return repository.delete_lead(ctx,i,l)
def add_document(ctx,i,p):return repository.create_child(ctx,'property_documents',i,dump(p))
def update_document(ctx,i,p):return repository.update_child(ctx,'property_documents',i,dump(p,True),'document')
def delete_document(ctx,i):return repository.delete_child(ctx,'property_documents',i,'document')
def add_photo(ctx,i,p):return repository.create_child(ctx,'property_photos',i,dump(p))
def update_photo(ctx,i,p):return repository.update_child(ctx,'property_photos',i,dump(p,True),'photo')
def delete_photo(ctx,i):return repository.delete_child(ctx,'property_photos',i,'photo')
def list_visits(*a,**k):return repository.list_visits(*a,**k)
def list_visits_by_contact(*a,**k):return repository.list_visits_by_contact(*a,**k)
def add_visit(ctx,i,p):return repository.add_visit(ctx,i,dump(p))
def update_visit(ctx,i,p):return repository.update_visit(ctx,i,dump(p,True))
def delete_visit(ctx,i):return repository.delete_visit(ctx,i)
def dashboard(ctx):return repository.dashboard(ctx)
def alerts(ctx):return repository.alerts(ctx)
