"""Closed-world v2.2 resolution authority, separate from inherited v2.1 proofs.

The frozen catalogue binds exact task, answer, native lineage and evidence.
Computing a new semantic signature cannot grant admission.
"""
from pathlib import Path
import hashlib
import json
try:
    from .review_binding import review_binding, canonical_json_bytes
    from .source_admission_v21 import _bound, _rows, AUDITS
    from .resolution_authority import CATALOGUE_SHA256, PAIR_CATALOGUE_SHA256, BASELINE
except ImportError:
    from review_binding import review_binding, canonical_json_bytes
    from source_admission_v21 import _bound, _rows, AUDITS
    from resolution_authority import CATALOGUE_SHA256, PAIR_CATALOGUE_SHA256, BASELINE

SCHEMA = 'public-transcription-v22-resolution-proof-v1'
SOURCE_FIELDS = ('source_binding', 'geometry_transform', 'controlled_pair_source')


def require(ok, message):
    if not ok:
        raise ValueError('v2.2 resolution: ' + message)


def validate_resolution(root, row, gold, record, cache=None):
    cache = {} if cache is None else cache
    root = Path(root).resolve()
    ref = record.get('source_evidence', {})
    rel = Path(ref.get('path', ''))
    require(bool(ref.get('path')) and not rel.is_absolute() and '..' not in rel.parts, 'unsafe proof path')
    p = (root / rel).resolve()
    require(p.is_relative_to(root), 'proof escapes package')
    p = _bound(p, ref.get('sha256'), cache)
    proof = json.loads(p.read_text('utf-8'))
    uid = row['sample_uid']
    require(proof.get('schema') == SCHEMA and proof.get('sample_uid') == uid, 'proof UID/schema')
    pair_transfer=proof.get('catalogue_sha256')==PAIR_CATALOGUE_SHA256 and PAIR_CATALOGUE_SHA256!='UNFROZEN'
    approved=PAIR_CATALOGUE_SHA256 if pair_transfer else CATALOGUE_SHA256
    require(proof.get('catalogue_sha256') == approved, 'unapproved catalogue')
    catalogue = _rows(root / ('sources/pair-transfer-catalogue.jsonl' if pair_transfer else 'sources/resolution-catalogue.jsonl'), approved, cache)
    require(uid in catalogue, 'UID has no adjudicated resolution')
    expected = catalogue[uid]
    require(expected.get('eligible') is True and gold.get('eligible') is True, 'not admitted')
    require(gold.get('review_status') == expected['review_status'], 'wrong provenance tier')
    require(record.get('resolution_admitted') is True, 'resolution provenance marker missing')
    binding = review_binding(row, gold)
    require(binding == expected['review_binding'], 'task/image/reference differs from adjudication')
    require(proof.get('review_binding_sha256') == hashlib.sha256(canonical_json_bytes(binding)).hexdigest(), 'semantic proof differs')
    for field in SOURCE_FIELDS:
        require(record.get(field) == expected.get(field), 'source/geometry drift: ' + field)
    require(gold.get('source_binding') == expected['source_binding'], 'gold provenance drift')
    require(row.get('metadata') == expected['metadata'], 'task-role/provenance metadata drift')
    require(row.get('legacy_uid') == expected['legacy_uid'], 'legacy lineage drift')
    require(row.get('image') == expected['image'], 'image path drift')
    require(proof.get('human_verified') is False, 'human certification invented')
    require(not record.get('quarantine_reasons'), 'active quarantine cannot be admitted')
    baseline = proof.get('baseline', {})
    require(baseline.get('fingerprints') == BASELINE, 'unapproved v2.1 baseline')
    tables = {name: _rows(Path(baseline['root']) / name, digest, cache) for name, digest in BASELINE.items()}
    core_uid = expected['original_core_uid']
    original = tables['annotations/records.jsonl'].get(core_uid)
    require(original is not None and original['protocol'] != 'PAIRED_INTERFERENCE', 'original core absent')
    require(original['legacy_uid'] == expected['legacy_uid'], 'original legacy differs')
    require(original['eligible'] is False, 'resolution cannot re-sign already admitted task')
    baseline_view=tables['annotations/records.jsonl'].get(uid)
    require(baseline_view is not None,'unknown baseline view')
    for field in ('protocol','category_path','group_id','pair_id','pair_variant'):
        require(row.get(field)==baseline_view.get(field),'original task lineage drift: '+field)
    audit_ref = expected['audit']
    require(audit_ref['sha256'] in AUDITS, 'unapproved original audit')
    audits = _rows(audit_ref['path'], audit_ref['sha256'], cache)
    audit = audits.get(core_uid)
    require(audit is not None and audit.get('legacy_uid') == original['legacy_uid'], 'original audit lineage')
    require(not audit.get('errors') and not audit.get('failures') and not audit.get('source_errors'), 'original source audit failed')
    for dep in expected['evidence']:
        _bound(dep['path'], dep['sha256'], cache)
    if pair_transfer:
        require(row['protocol']=='PAIRED_INTERFERENCE','pair authority is only for paired views')
        require(record.get('resolution_pair_core_uid')==core_uid,'pair transfer core differs')
        require(record.get('resolution_pair_transfer') is True,'pair transfer marker missing')
        current={name:_rows(root/name,hashlib.sha256((root/name).read_bytes()).hexdigest(),cache)
                 for name in ('inputs/model-inputs.jsonl','annotations/gold.jsonl','annotations/records.jsonl')}
        ci,cg,cr=(current[name][core_uid] for name in ('inputs/model-inputs.jsonl','annotations/gold.jsonl','annotations/records.jsonl'))
        require(ci['protocol']!='PAIRED_INTERFERENCE','pair cannot transfer from another pair')
        require(review_binding(ci,cg)==expected['core_review_binding'],'current core differs from transfer evidence')
        validate_resolution(root,ci,cg,cr,cache)
        require(gold['reference_text']==cg['reference_text'] and gold['reference_format']==cg['reference_format'],
                'pair target/reference differs from current core')
        require(record['source_binding']==cr['source_binding'],'pair/current core source differs')
        require(record['geometry_transform'].get('pair_qualification',{}).get('qualified') is True,'pair is not qualified')
    return proof


def validate_inherited_visual(root, row, gold, record, cache=None):
    """Only unchanged, actually admitted v2.1 visual rows inherit that tier."""
    cache={} if cache is None else cache
    descriptor=json.loads((Path(root)/'dataset.json').read_text('utf-8'))
    baseline=Path(descriptor['parent_candidate'])
    tables={name:_rows(baseline/name,digest,cache) for name,digest in BASELINE.items()}
    uid=row['sample_uid']
    old=tables['annotations/records.jsonl'].get(uid)
    require(old is not None and old.get('eligible') is True and
            old.get('review_status') in {'REVIEWED','CORRECTED_REVIEWED'},
            'no inherited visual admission for UID')
    require(review_binding(row,gold)==review_binding(old,old),'inherited visual semantic drift')
    require(gold.get('review_status')==old['review_status'],'inherited visual tier drift')
    require(record.get('review_evidence')==old.get('review_evidence'),'inherited visual proof drift')
    require(row==tables['inputs/model-inputs.jsonl'][uid],'inherited visual input/metadata drift')
    require(gold.get('source_binding')==old.get('source_binding'),'inherited visual gold provenance drift')
    for field in SOURCE_FIELDS:require(record.get(field)==old.get(field),'inherited source/geometry drift')
    return old
