"""Content-and-scope binding for v2 canonical visual-review proofs (CPU only).

This is an integrity check, not a visual reviewer or an admission authority.
The caller must still verify the canonical receipt's file hash, its raw receipt
hash and provenance, and the actual image bytes/dimensions. A self-consistent
binding does not prove that a reviewer saw the image or reviewed the whole page.

Canonical representation: exact Unicode strings (no normalization), JSON encoded
as UTF-8 with ensure_ascii=False, sort_keys=True, separators=(',', ':'), and
allow_nan=False. Object key order is irrelevant; ALL list orders are significant.
Absent non-page blocks and explicit blocks=None both bind the JSON value null.
Review status/eligibility, receipt fields, paths and descriptive metadata are not
part of this digest, so adding a proof or moving files cannot create a cycle.
Source provenance remains a separate package check: semantic visual facts are
bound to the actual input image digest, dimensions and complete task, rather than
to a source path or an annotation-container filename/hash.

Legacy receipt migration policy (no automatic upgrade is implemented):

1. Preserve and SHA256-bind the original raw receipt and dispatch descriptor.
   Require an actual PASS for this exact target. A summary count, a correction
   suggestion, or a previous NEEDS_CORRECTION/DEFER/UNREADABLE is not a PASS.
   Conflicts require the independently reviewed supplement and its source links.
2. Recover the frozen reviewed input/gold snapshot. Verify sample UID, actual
   image hash, dimensions, exact UTF-8 reference hash, and wholeblocks hash
   (including null), plus the reviewed ROI/protocol/scope. Recover instruction
   and output/reference formats from the original frozen task/contract when the
   raw receipt omits them; never infer them from the new candidate alone.
3. Root independently verifies pixel equivalence and scope transfer against the
   candidate. Record checks for ROI coverage, formatting, complete PAGE blocks
   and their order. Equal image/reference hashes alone cannot establish this.
   Changed UID, task, ROI, protocol, format, image, or blocks needs a NEW canonical
   proof with explicit transfer evidence or new review; old proof is not reused.
4. Preserve the raw receipt, original snapshot/descriptor and their hashes. The
   new root transfer receipt records the old and new binding objects/hashes,
   pixel/scope checks, covered limitations, reviewer, and evidence links/hashes.
   Attach that transfer evidence to the new canonical proof, then add the binding
   fields below. Generating these fields itself is never evidence of review.
5. If image/reference/wholeblocks or ROI/scope evidence is missing, retain the
   item pending/quarantined. Do not synthesize evidence or silently upgrade a
   legacy proof that only contains image_sha256 and reference_sha256.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
import re


BINDING_SCHEMA = 'public-transcription-v2-semantic-review-binding-v1'
PROTOCOLS = {'REGION_TRANSCRIPTION', 'PAGE_TRANSCRIPTION', 'PAIRED_INTERFERENCE'}
FORMATS = {'TEXT', 'LATEX', 'TABLE_HTML', 'BLOCKS'}
_SHA256 = re.compile(r'[0-9a-f]{64}')


def _check_json(value, path='$'):
    """Reject implicit JSON coercions, nonfinite numbers and non-string keys."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(path + ': nonfinite JSON number')
        return
    if type(value) is list:
        for index, child in enumerate(value):
            _check_json(child, path + '[' + str(index) + ']')
        return
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError(path + ': JSON object key must be a string')
            _check_json(child, path + '.' + key)
        return
    raise ValueError(path + ': value must use native JSON types')


def canonical_json_bytes(value):
    """Return the one byte representation used for every semantic binding."""
    _check_json(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _nonempty_string(value, field):
    if type(value) is not str or not value.strip():
        raise ValueError(field + ': nonempty string required')


def review_binding(input_row, gold_row):
    """Snapshot every reviewed input/scope/answer field, excluding admission state.

    This can accept the same combined record as both arguments. It deliberately
    preserves all task and block fields, so future scope fields cannot disappear
    through an allow-list. Broader package/schema validation remains the caller's
    responsibility. No file is opened and no review decision is generated.
    """
    if not isinstance(input_row, Mapping) or not isinstance(gold_row, Mapping):
        raise ValueError('input and gold must be objects')
    uid = input_row.get('sample_uid')
    _nonempty_string(uid, 'sample_uid')
    if gold_row.get('sample_uid') != uid:
        raise ValueError('input/gold sample_uid differs')
    image_sha = input_row.get('image_sha256')
    if type(image_sha) is not str or _SHA256.fullmatch(image_sha) is None:
        raise ValueError('image_sha256: lowercase full SHA256 required')
    for field in ('width', 'height'):
        if type(input_row.get(field)) is not int or input_row[field] <= 0:
            raise ValueError(field + ': positive integer required')
    protocol = input_row.get('protocol')
    if type(protocol) is not str or protocol not in PROTOCOLS:
        raise ValueError('protocol: unsupported/missing protocol')
    task = input_row.get('task')
    if type(task) is not dict:
        raise ValueError('task: object required')
    for field in ('instruction', 'output_format'):
        _nonempty_string(task.get(field), 'task.' + field)
    if type(task.get('target_regions')) is not list:
        raise ValueError('task.target_regions: explicit list required')
    reference_format = gold_row.get('reference_format')
    if type(reference_format) is not str or reference_format not in FORMATS:
        raise ValueError('reference_format: unsupported/missing format')
    if type(gold_row.get('reference_text')) is not str:
        raise ValueError('reference_text: string required')
    blocks = gold_row.get('blocks')
    if blocks is not None and type(blocks) is not list:
        raise ValueError('blocks: list or null required')
    if protocol == 'PAGE_TRANSCRIPTION' and (reference_format != 'BLOCKS' or blocks is None):
        raise ValueError('PAGE requires BLOCKS format and explicit ordered blocks')
    if reference_format == 'BLOCKS' and blocks is None:
        raise ValueError('BLOCKS reference requires explicit ordered blocks')
    if blocks is not None:
        for block in blocks:
            if type(block) is not dict:
                raise ValueError('block: object required')
            _nonempty_string(block.get('region_id'), 'block.region_id')
            if type(block.get('text')) is not str:
                raise ValueError('block.text: string required')
            block_format = block.get('reference_format')
            if type(block_format) is not str or block_format not in FORMATS - {'BLOCKS'}:
                raise ValueError('block.reference_format: invalid/missing format')
    payload = {
        'schema': BINDING_SCHEMA,
        'sample_uid': uid,
        'image_sha256': image_sha,
        'width': input_row['width'],
        'height': input_row['height'],
        'protocol': protocol,
        'task': task,
        'reference_format': reference_format,
        'reference_text': gold_row['reference_text'],
        'blocks': blocks,
    }
    # A detached snapshot prevents later mutation of input/task/blocks from
    # retroactively changing an already-created proof object.
    return json.loads(canonical_json_bytes(payload))


def review_binding_sha256(input_row, gold_row):
    """Compute a full SHA256 for the exact content and task under review."""
    return hashlib.sha256(canonical_json_bytes(review_binding(input_row, gold_row))).hexdigest()


def review_binding_fields(input_row, gold_row):
    """Return fields for an independently authorized NEW canonical proof.

    This helper must not be used to auto-promote an unbound legacy receipt.
    """
    binding = review_binding(input_row, gold_row)
    return {'review_binding': binding,
            'review_binding_sha256': hashlib.sha256(canonical_json_bytes(binding)).hexdigest()}


def validate_review_proof(proof, input_row, gold_row):
    """Require PASS admission and exact complete binding; raise ValueError on drift.

    The full embedded payload is required as well as its digest, permitting
    independent inspection and preventing omitted fields from being accepted.
    Old aggregate-text-only proofs fail closed. Returns None on success.
    """
    if not isinstance(proof, Mapping):
        raise ValueError('review proof must be an object')
    if proof.get('verdict') != 'PASS' or proof.get('eligible') is not True:
        raise ValueError('review proof must have PASS verdict and eligible=true')
    expected = review_binding(input_row, gold_row)
    if proof.get('sample_uid') != expected['sample_uid']:
        raise ValueError('review proof sample_uid differs')
    actual = proof.get('review_binding')
    if type(actual) is not dict:
        raise ValueError('complete semantic review_binding required; legacy proof cannot auto-upgrade')
    expected_bytes = canonical_json_bytes(expected)
    if canonical_json_bytes(actual) != expected_bytes:
        raise ValueError('semantic review binding differs from current input/gold')
    digest = proof.get('review_binding_sha256')
    if type(digest) is not str or _SHA256.fullmatch(digest) is None:
        raise ValueError('full semantic review_binding_sha256 required')
    if digest != hashlib.sha256(expected_bytes).hexdigest():
        raise ValueError('semantic review binding SHA256 differs')
