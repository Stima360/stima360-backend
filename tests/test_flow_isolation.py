from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SQL=(ROOT/'migrations/008_flow_01.sql').read_text().lower()
DOWN=(ROOT/'migrations/008_flow_01_down.sql').read_text().lower()

def test_migration_only_flow_tables():
    assert 'alter table contacts' not in SQL and 'alter table properties' not in SQL and 'alter table buy_requests' not in SQL and 'alter table matches' not in SQL
def test_no_queue_or_channels():
    assert 'celery' not in SQL and 'redis' not in SQL and 'whatsapp' not in SQL and 'email' not in SQL
def test_retry_limit_database_constraint(): assert 'retry_count between 0 and 3' in SQL and 'max_retry = 3' in SQL
def test_rollback_only_flow_tables():
    assert all(f'drop table if exists {x}' in DOWN for x in ['flow_rules','flow_events','flow_executions','flow_action_records','flow_suppressions'])
def test_main_not_in_package(): assert not (ROOT/'main.py').exists()
def test_rule_logic_not_stored_freely(): assert 'conditions jsonb' not in SQL and 'actions jsonb' not in SQL
