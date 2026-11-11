"""Use a pinned fresh visual receipt for the one v2.3 repair."""
import json
from pathlib import Path

try:
    from .resolution_admission import validate_inherited_visual
    from .review_binding import validate_review_proof
    from .source_admission_v21 import _bound
except ImportError:
    from resolution_admission import validate_inherited_visual
    from review_binding import validate_review_proof
    from source_admission_v21 import _bound


FINAL_UID = 'v2-pub-D0PL-0061-page'
FINAL_RECEIPT_SHA256 = '3896c7b7688f2053af90295438a48706060fba4e2479454eef2b6cbaa36f29c6'


def require(condition, reason):
    if not condition:
        raise ValueError('v2.3 visual admission: ' + reason)


def validate_visual(root, row, gold, record, cache=None):
    if row['sample_uid'] != FINAL_UID:
        return validate_inherited_visual(root, row, gold, record, cache)
    cache = {} if cache is None else cache
    root = Path(root).resolve()
    relative = 'review/' + FINAL_UID + '.json'
    proof_path = _bound(root / relative, FINAL_RECEIPT_SHA256, cache)
    proof = json.loads(proof_path.read_text('utf-8'))
    require(gold.get('review_status') == record.get('review_status') == 'CORRECTED_REVIEWED' and
            gold.get('eligible') is record.get('eligible') is True and
            not gold.get('quarantine_reasons') and not record.get('quarantine_reasons'),
            'incorrect eligibility tier')
    require(record.get('v23_full_target_visual') is True and not record.get('resolution_admitted'),
            'incorrect admission scope')
    require(record.get('review_evidence') == [dict(
        path=relative, sha256=FINAL_RECEIPT_SHA256, image_sha256=proof['image_sha256'],
        reference_sha256=proof['reference_sha256'])], 'canonical receipt pointer drift')
    require(proof['human_verified'] is False and proof['image_path'] == row['image'] and
            proof['source_binding'] == gold['source_binding'] == record['source_binding'], 'source provenance drift')
    validate_review_proof(proof, row, gold)
    raw_name = 'raw-v23-final-' + FINAL_UID + '.json'
    require(proof['raw_receipt_filename'] == raw_name, 'incorrect raw receipt')
    raw_path = _bound(proof_path.with_name(raw_name), proof['raw_receipt_sha256'], cache)
    raw = json.loads(raw_path.read_text('utf-8'))
    require(raw['sample_uid'] == FINAL_UID and raw['verdict'] == 'PASS' and
            raw['complete_necessary_coverage'] is True and raw['human_verified'] is False and
            not raw['unresolved'], 'raw review does not qualify')
    require(raw['input_image_sha256'] == row['image_sha256'] and raw['input_task'] == row['task'] and
            raw['reference_text'] == gold['reference_text'] and raw['blocks'] == gold['blocks'],
            'raw review differs from scoring target')
    source = proof['source_image']
    require(raw['source_image'] == source and source['path'] == record['source_binding']['image_path'] and
            source['sha256'] == record['source_binding']['image_sha256'], 'original image binding drift')
    _bound(source['path'], source['sha256'], cache)
    return proof
