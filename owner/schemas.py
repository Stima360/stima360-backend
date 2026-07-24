from pydantic import BaseModel,Field,ConfigDict
from typing import Literal
from datetime import datetime
class M(BaseModel): model_config=ConfigDict(from_attributes=True)
class AccountCreate(M): contact_id:int; preferred_language:str='it'
class AccessCreate(M): owner_account_id:int;property_id:int;access_role:Literal['owner','co_owner','delegate','legal_representative']='owner';is_primary:bool=False;valid_until:datetime|None=None
class TokenCreate(M): token_type:Literal['invitation','login']='login';expires_minutes:int=Field(30,ge=5,le=1440);created_by:str|None=None
class TokenConsume(M): token:str=Field(min_length=32,max_length=512)
class PublicationCreate(M):
 property_id:int;publication_type:Literal['general_update','marketing_update','visit_update','feedback_summary','strategy_update','milestone'];title:str=Field(min_length=1,max_length=200);summary:str|None=Field(None,max_length=1000);body:str=Field(min_length=1,max_length=20000)
class PublicationUpdate(M):
 publication_type:Literal['general_update','marketing_update','visit_update','feedback_summary','strategy_update','milestone']|None=None;title:str|None=Field(None,min_length=1,max_length=200);summary:str|None=Field(None,max_length=1000);body:str|None=Field(None,min_length=1,max_length=20000)
class FeedbackCreate(M): feedback_type:Literal['contact_request','correction_request','general_message','strategy_feedback'];subject:str=Field(min_length=1,max_length=150);message:str=Field(min_length=1,max_length=5000)
class FeedbackStatus(M): status:Literal['new','in_review','handled','closed'];handled_by:str|None=None
