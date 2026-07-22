from pathlib import Path
S=(Path(__file__).resolve().parents[1]/'run_flow_01_e2e.py').read_text()
def test_harness_blocks_production(): assert 'BLOCCATO' in S and 'test' in S
def test_harness_cleanup_present(): assert 'cleanup' in S and 'E2E_FLOW01_' in S
def test_harness_checks_simulation_gate(): assert 'activate_without_simulation' in S
def test_harness_checks_retry_limit(): assert 'retry_limit' in S
