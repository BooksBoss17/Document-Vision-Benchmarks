"""Source admission, distinct from visual review, for the frozen v2.1 migration.

Only the three completed original-source audits and the immutable v2 baseline
can authorize this transfer. No pixel decoding or OCR is performed here.
"""
import hashlib
import json
import copy
import re
from pathlib import Path

try:
    from .review_binding import canonical_json_bytes, review_binding, review_binding_fields
except ImportError:
    from review_binding import canonical_json_bytes, review_binding, review_binding_fields

SCHEMA = 'public-transcription-v21-source-proof-v1'
AUDITS = {
    'd8ecaac596b4dcc9890027dde7ee924c60b08430c5629f43ae23e579c5f3a53e': 'omni',
    'af4a5ad5dee9f069b84c21b00f3dd4dc44cde2f61c82eb81507cbbb6bcbce96f': 'exam',
    'aab6ee69b48e25498bcbf5cfafa2b75b1d7c0a6facb405ce363dc97ead7d6425': 'other',
}
BASELINE = {
    'inputs/model-inputs.jsonl': '5aa876245e01f8c134208983689cd98f4b9e85d6dab8e0d06508f108d337a71f',
    'annotations/gold.jsonl': '39f1b7e8164f21100c29007467b4e9a2b0d369256a35e4aee38b7317417a53f8',
    'annotations/records.jsonl': '9ffd47f40a628c1102d6d98cfe50583d1b6353bd5e00f24b26221d8a2784854f',
}
PIXEL_RECEIPT_SHA = '1d6c4b99ed1f1c2c86282043a8579e0ae4269071ffa4103fd19fa2975569a6e0'
SCOPES = {'ANNOTATED_REGIONS', 'SOURCE_ANNOTATED_REGION', 'UPSTREAM_TARGET_REFERENCE'}
VISUAL_DEFER_ONLY_TRANSFER = 'v2-pub-D0HL-0084-page'
_MAKE_HASHES = {}
PROFILE = 'UPSTREAM_TEX_MIXED_V1'
TEX_INSTRUCTION = (' Use serialization profile UPSTREAM_TEX_MIXED_V1. Preserve the upstream TeX serialization of mathematical spans, including '
                   'control words, braces and delimiters, alongside ordinary text. Do not '
                   'evaluate, simplify, convert TeX to Unicode, or infer unreadable content. '
                   'Scoring uses conservative TeX-aware tokens, without mathematical equivalence.')
PAGE_INSTRUCTION = ('Transcribe only the requested upstream annotated regions. Return JSON '
                    'blocks with region_id and text. Do not add unannotated content or blocks '
                    'outside the requested regions. Preserve the source annotated reading order '
                    'and use region_formats for each block. Regions in unordered_region_ids '
                    'have no unique linear order; include each such region in any order. '
                    'Preserve symbols and structure; do not infer missing content.')


def native_task_contract(input_row, gold_row, source):
    """The only permitted native-task migration, shared by builder and verifier."""
    _require(source in {'omni', 'exam', 'other'}, 'unknown contract source')
    task = copy.deepcopy(input_row['task'])
    if input_row['protocol'] == 'PAGE_TRANSCRIPTION':
        task['scope'] = 'ANNOTATED_REGIONS'
        task['instruction'] = PAGE_INSTRUCTION
    else:
        task['scope'] = 'UPSTREAM_TARGET_REFERENCE' if source == 'exam' else 'SOURCE_ANNOTATED_REGION'
    texts = [gold_row['reference_text']] if gold_row['reference_format'] == 'TEXT' else [
        b['text'] for b in gold_row.get('blocks', []) if b['reference_format'] == 'TEXT']
    if any(re.search(r'\\[A-Za-z]+', text) for text in texts):
        task['serialization_profile'] = PROFILE
        task['instruction'] += TEX_INSTRUCTION
    return task


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _require(ok, message):
    if not ok:
        raise ValueError('source admission: ' + message)


def _bound(path, digest, cache):
    path = str(Path(path).resolve())
    files = cache.setdefault('files', {})
    if path not in files:
        _require(sha(path) == digest, 'dependency SHA differs: ' + path)
        files[path] = digest
    _require(files[path] == digest, 'inconsistent dependency hash')
    return Path(path)


def _rows(path, digest, cache):
    p = _bound(path, digest, cache)
    parsed = cache.setdefault('rows', {})
    if str(p) not in parsed:
        rows = [json.loads(x) for x in p.read_text('utf-8-sig').splitlines() if x.strip()]
        keys = [r['sample_uid'] for r in rows]
        _require(len(keys) == len(set(keys)), 'duplicate dependency UID')
        parsed[str(p)] = dict(zip(keys, rows))
    return parsed[str(p)]


def make_source_proof(row, gold, audit_path, core_uid, baseline_root, policy, pair_core_uid=None):
    """Assemble a proof; validate_source_proof independently checks its authority."""
    audit_key = str(Path(audit_path).resolve())
    if audit_key not in _MAKE_HASHES:
        _MAKE_HASHES[audit_key] = sha(audit_path)
    audit_hash = _MAKE_HASHES[audit_key]
    result = dict(schema=SCHEMA, sample_uid=row['sample_uid'], verdict='SOURCE_VERIFIED',
                  human_verified=False,
                  audit=dict(path=str(Path(audit_path).resolve()), sha256=audit_hash, row_uid=core_uid),
                  baseline=dict(root=str(Path(baseline_root).resolve()), core_uid=core_uid,
                                inputs_sha256=BASELINE['inputs/model-inputs.jsonl'],
                                gold_sha256=BASELINE['annotations/gold.jsonl'],
                                records_sha256=BASELINE['annotations/records.jsonl']),
                  policy=policy, **review_binding_fields(row, gold))
    if pair_core_uid is not None:
        result['pair_source_core_uid'] = pair_core_uid
    return result


def _audit_allowed(audit, kind):
    if kind == 'omni':
        _require(not audit.get('errors'), 'original audit errors')
        _require(audit.get('disposition') in {'SOURCE_ANNOTATED_ROI_SUPPORTS_SCORING',
                 'V2_PAGE_SCOPE_REQUIRES_COVERAGE_OR_ALIGNMENT'}, 'Omni audit did not support transfer')
        _require(audit.get('native_source', {}).get('binding_verified') is True, 'Omni source mapping absent')
        _require(audit.get('current_image', {}).get('bytes_and_dimensions_verified') is True, 'Omni image mapping absent')
        _require(not audit.get('native_referenced_blocks_missing_from_page_task'), 'native blocks missing')
        _require(not audit.get('task_scope', {}).get('native_ignore_ids_in_current_targets'), 'ignored targets unresolved')
        _require(all(c.get('status') != 'FAIL' for c in audit.get('serialization_checks', [])), 'invalid source serialization')
    elif kind == 'exam':
        _require(not audit.get('failures') and all(audit.get('checks', {}).values()), 'Exam mapping failed')
        _require(audit.get('primary_state') not in {'EXISTING_QUARANTINE', 'NOT_TRANSCRIPTION_TASK'}, 'unsupported or quarantined native task')
        _require(audit.get('native_ground_truth', {}).get('target_type') != 'StudentCorrection', 'category label is not transcription')
        _require('FORMULA_TYPED_REFERENCE_CONTAINS_PROSE_REQUIRES_SCOPE_CHECK' not in audit.get('content_risks', []), 'formula/prose task unresolved')
        _require(audit.get('source_comparison') == 'SOURCE_CONSISTENT', 'changed native answer requires separate review')
    else:
        _require(audit.get('source_alignment_status') == 'PASS' and not audit.get('source_errors'), 'native source mapping failed')
        _require(audit.get('disposition') == 'SOURCE_ALIGNED_VISUAL_REVIEW_ONLY', 'source special convention or content issue unresolved')
        _require(not audit.get('ffff_tokens'), 'FFFF region semantics unresolved')


def validate_source_proof(root, input_row, gold_row, record, cache=None):
    """Fail closed on unsupported tasks, fabricated authority, or task/answer drift.

    ``cache['files']`` enumerates every evidence dependency for scorer sealing.
    The frozen audits bind checked original bytes/mapping; those facts are
    transferred, not represented as a fresh visual review or pixel decode.
    """
    cache = {} if cache is None else cache
    ref = record.get('source_evidence', {})
    rel = Path(ref.get('path', ''))
    _require(bool(ref.get('path')) and not rel.is_absolute() and '..' not in rel.parts, 'unsafe/missing source evidence')
    path = (Path(root) / rel).resolve()
    _require(path.is_relative_to(Path(root).resolve()), 'evidence escapes package')
    path = _bound(path, ref.get('sha256'), cache)
    proof = json.loads(path.read_text('utf-8'))
    uid = input_row.get('sample_uid')
    _require(proof.get('schema') == SCHEMA and proof.get('sample_uid') == uid, 'proof schema or UID')
    _require(proof.get('verdict') == 'SOURCE_VERIFIED' and proof.get('human_verified') is False, 'wrong source tier')
    _require(gold_row.get('eligible') is True and gold_row.get('review_status') == 'SOURCE_VERIFIED', 'wrong admission state')
    binding = review_binding(input_row, gold_row)
    _require(proof.get('review_binding') == binding, 'current semantic binding differs')
    _require(proof.get('review_binding_sha256') == hashlib.sha256(canonical_json_bytes(binding)).hexdigest(), 'semantic hash differs')
    audit_ref = proof.get('audit', {})
    digest = audit_ref.get('sha256')
    _require(digest in AUDITS, 'unknown/unapproved source audit')
    audits = _rows(audit_ref.get('path'), digest, cache)
    core_uid = proof.get('baseline', {}).get('core_uid')
    _require(audit_ref.get('row_uid') == core_uid and core_uid in audits, 'audit UID mismatch')
    audit = audits[core_uid]
    _audit_allowed(audit, AUDITS[digest])
    baseline = proof['baseline']
    for field, name in [('inputs_sha256', 'inputs/model-inputs.jsonl'), ('gold_sha256', 'annotations/gold.jsonl'), ('records_sha256', 'annotations/records.jsonl')]:
        _require(baseline.get(field) == BASELINE[name], 'unapproved baseline')
    tables = [_rows(Path(baseline['root']) / name, digest, cache) for name, digest in BASELINE.items()]
    bi, bg, br = tables
    _require(uid in bi and core_uid in bi and bi[core_uid]['protocol'] != 'PAIRED_INTERFERENCE', 'unknown baseline/core UID')
    old, oldgold, oldrec = bi[uid], bg[uid], br[uid]
    _require(old.get('legacy_uid') == bi[core_uid].get('legacy_uid') == audit.get('legacy_uid'), 'source lineage differs')
    for key in ('sample_uid', 'legacy_uid', 'protocol', 'image', 'image_sha256', 'width', 'height', 'group_id', 'pair_id', 'pair_variant'):
        _require(input_row.get(key) == old.get(key), 'baseline input differs: ' + key)
    for key in ('reference_text', 'reference_format', 'blocks', 'unordered_region_ids', 'source_binding'):
        _require(gold_row.get(key) == oldgold.get(key), 'baseline reference differs: ' + key)
    for key in ('geometry_transform', 'source_binding', 'controlled_pair_source'):
        _require(record.get(key) == oldrec.get(key), 'baseline source geometry differs: ' + key)
    old_task = dict(old['task'])
    task = dict(input_row['task'])
    for key in ('instruction', 'scope', 'serialization_profile'):
        old_task.pop(key, None)
        task.pop(key, None)
    _require(task == old_task, 'task target/output/order drift')
    scope = input_row['task'].get('scope')
    _require(scope in SCOPES, 'undeclared native scope')
    if input_row['protocol'] == 'PAGE_TRANSCRIPTION':
        _require(scope == 'ANNOTATED_REGIONS', 'PAGE must restrict to annotated regions')
    profile = input_row['task'].get('serialization_profile')
    _require(profile in (None, 'UPSTREAM_TEX_MIXED_V1'), 'unknown source serialization')
    _require(proof.get('policy', {}).get('scope') == scope, 'policy/task scope differs')
    if profile:
        _require(profile in input_row['task'].get('instruction', ''), 'profile not declared in model instruction')
    _require(input_row['task'] == native_task_contract(old, oldgold, AUDITS[digest]),
             'instruction/profile does not match exact native task contract')
    if input_row['protocol'] == 'PAIRED_INTERFERENCE':
        _require(proof.get('pair_source_core_uid') == core_uid, 'pair/core proof missing')
        _require(oldrec.get('geometry_transform', {}).get('pair_qualification', {}).get('qualified') is True, 'pair geometry unqualified')
        _require(oldgold.get('reference_format') == bg[core_uid].get('reference_format') and oldgold.get('reference_text') == bg[core_uid].get('reference_text'), 'pair/core reference differs')
        _require(oldrec.get('source_binding') == br[core_uid].get('source_binding'), 'pair/core native source differs')
    if oldrec.get('quarantine_reasons'):
        # Root-authorized, exact audited exception: two ambiguous glyphs were
        # deferred by AI, but no GT error or geometry issue was established.
        # 0098 and all other geometric/content quarantines remain excluded.
        _require(uid == core_uid == VISUAL_DEFER_ONLY_TRANSFER and AUDITS[digest] == 'other'
                 and oldrec.get('quarantine_reasons') == ['VISUAL_DEFER']
                 and audit.get('disposition') == 'SOURCE_ALIGNED_VISUAL_REVIEW_ONLY'
                 and audit.get('native_gt_visual_discrepancy') == 'NO_ACTUAL_GT_ERROR_ESTABLISHED_IN_THIS_AUDIT',
                 'baseline quarantine cannot be source-promoted')
        _require(record.get('visual_review_status') == oldrec.get('review_status')
                 and record.get('historical_quarantine_reasons') == ['VISUAL_DEFER']
                 and not record.get('quarantine_reasons'),
                 'visual defer must remain explicit after source admission')
    return proof
