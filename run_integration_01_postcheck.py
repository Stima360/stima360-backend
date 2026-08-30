#!/usr/bin/env python3
from __future__ import annotations
import json, os
from pathlib import Path
from integration_p2_support import OriginalRecord, PKRecord, RunManifest, TestResult, postcheck, require_test_environment

def load(path: Path) -> RunManifest:
    raw=json.loads(path.read_text(encoding="utf-8"))
    raw["tests"]=[TestResult(**x) for x in raw.get("tests",[])]
    raw["created_pks"]=[PKRecord(**x) for x in raw.get("created_pks",[])]
    raw["original_records"]=[OriginalRecord(**x) for x in raw.get("original_records",[])]
    return RunManifest(**raw)

def main():
    require_test_environment(require_http=True,require_branch=True)
    path=Path(os.environ.get("INTEGRATION_MANIFEST_PATH", ""))
    if not path.is_file(): raise SystemExit("BLOCCATO: indicare INTEGRATION_MANIFEST_PATH esistente")
    manifest=load(path)
    result=postcheck(manifest)
    print(json.dumps(result,indent=2,ensure_ascii=False))

if __name__=="__main__": main()
