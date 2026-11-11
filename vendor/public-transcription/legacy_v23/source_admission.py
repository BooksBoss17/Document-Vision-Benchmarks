"""Dispatch inherited v2.1 source proofs and frozen v2.2 adjudications."""
import json
from pathlib import Path
try:
    from .source_admission_v21 import validate_source_proof as validate_inherited
    from .resolution_admission import validate_resolution, SCHEMA
except ImportError:
    from source_admission_v21 import validate_source_proof as validate_inherited
    from resolution_admission import validate_resolution, SCHEMA


def validate_source_proof(root, input_row, gold_row, record, cache=None):
    ref = record.get('source_evidence', {})
    rel = Path(ref.get('path', ''))
    target = (Path(root) / rel).resolve()
    if not ref.get('path') or rel.is_absolute() or '..' in rel.parts or not target.is_relative_to(Path(root).resolve()):
        raise ValueError('unsafe source proof path')
    from . import source_admission_v21 as inherited_source
    target = inherited_source._bound(target, ref['sha256'], {} if cache is None else cache)
    proof = json.loads(target.read_text('utf-8'))
    if proof.get('schema') == SCHEMA:
        return validate_resolution(root, input_row, gold_row, record, cache)
    return validate_inherited(root, input_row, gold_row, record, cache)
