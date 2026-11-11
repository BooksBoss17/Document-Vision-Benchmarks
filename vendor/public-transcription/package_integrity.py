"""Pinned core contracts plus full checksums; explicit official/custom scope."""
from pathlib import Path
import json,hashlib
from v24_authority import CORE_FILES,OFFICIAL_SELECTIONS

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()

def safe(root,rel):
    p=(root/rel).resolve()
    if Path(rel).is_absolute() or '..' in Path(rel).parts or not p.is_relative_to(root):raise ValueError('package path escapes root: '+rel)
    return p

def verify_package(root,public_only=False):
    root=Path(root).resolve()
    for rel,digest in CORE_FILES.items():
        if public_only and not (rel.startswith(('inputs/','selections/','tools/')) or rel=='dataset.json'):continue
        if sha(safe(root,rel))!=digest:raise ValueError('pinned v2.4 bytes changed: '+rel)
    d=json.loads((root/'dataset.json').read_text(encoding='utf-8'))
    if d['version']!='2.4.0' or d['total_samples']!=3039 or d['eligible_samples']!=2928 or d['human_verified'] is not False:raise ValueError('fixed v2.4 population/version contract changed')
    manifest=root/'checksums.sha256'
    if not manifest.is_file():raise ValueError('checksums.sha256 required')
    expected={}
    for line in manifest.read_text(encoding='utf-8').splitlines():
        digest,rel=line.split('  ',1)
        if rel in expected or len(digest)!=64:raise ValueError('invalid/duplicate checksum member')
        expected[rel]=digest
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name!='checksums.sha256'}
    if set(expected)!=actual:raise ValueError('full package checksum coverage differs')
    if not public_only:
        for rel,digest in expected.items():
            if sha(safe(root,rel))!=digest:raise ValueError('package checksum differs: '+rel)
    return {'manifest_sha256':sha(manifest),'members':len(expected),'full_content_hash_check':not public_only,
            'trust':'Core content/tool pins are part of this release. Compare manifest digest to independent registration receipt for official release identity; no engine execution attestation.'}

def selection_scope(selection):
    if selection is None:return 'FULL_INPUT_INVENTORY_WITH_INELIGIBLE_ITEMS'
    digest=sha(selection)
    names=[n for n,h in OFFICIAL_SELECTIONS.items() if h==digest]
    if 'admitted-all.json' in names:return 'OFFICIAL_DEFAULT_ADMITTED'
    if names:return 'OFFICIAL_REGISTERED_SUBSET:'+sorted(names)[0]
    return 'CUSTOM_SUBSET_NOT_OFFICIAL_SCORE'
