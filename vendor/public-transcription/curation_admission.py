"""Closed v2.4 curation authority over fully revalidated v2.3 ancestor evidence.

A transformed input never reuses its old visual binding as a new visual review.
The approved catalogue pins before/after triples and the explicit curation scope.
Fresh active-answer corrections additionally require the fixed visual receipt.
"""
from pathlib import Path
import json,hashlib,importlib
from runtime_access import Access,install_legacy,sha
from v24_authority import CATALOGUE_SHA256,PARENT_CONTRACT_SHA256,VISUAL_SHA256,NATIVE_ORDER_POLICY_SHA256
from native_order_policy import validate_native_order

_STATES={}
def canonical(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
def digest(x):return hashlib.sha256(canonical(x)).hexdigest()
def need(ok,msg):
    if not ok:raise ValueError('v2.4 curation admission: '+msg)
def rows(p):
    with p.open(encoding='utf-8') as stream:
        return {x['sample_uid']:x for x in map(json.loads,stream)}

def state(root):
    root=Path(root).resolve();key=str(root)
    if key in _STATES:return _STATES[key]
    access=Access(root);parent_ref=root/'sources/v23-parent-contract.json'
    need(sha(parent_ref)==PARENT_CONTRACT_SHA256,'parent contract SHA changed')
    parent=json.loads(parent_ref.read_text(encoding='utf-8'));parent_root=(access.workspace/parent['relative_workspace_root']).resolve()
    need(parent_root.is_relative_to(access.workspace),'parent escapes workspace')
    parent_files={name:access.locate(str(parent_root/name),h) for name,h in {'checksums.sha256':parent['checksums_sha256'],**parent['files']}.items()}
    need(sha(parent_files['checksums.sha256'])==parent['checksums_sha256'],'ancestor manifest changed')
    for name,h in parent['files'].items():need(sha(parent_files[name])==h,'ancestor bytes changed: '+name)
    catalogue=root/'sources/v23-to-v24-curation.jsonl';need(sha(catalogue)==CATALOGUE_SHA256,'unapproved curation catalogue')
    visual=root/'review/v24-focused-visual.json';need(sha(visual)==VISUAL_SHA256,'fresh visual receipt changed')
    geom=install_legacy(access)
    s={'root':root,'parent_root':parent_root,'access':access,'geometry':geom,
       'before':[rows(parent_files[f]) for f in ['inputs/model-inputs.jsonl','annotations/gold.jsonl','annotations/records.jsonl']],
       'curation':rows(catalogue),'visual':{x['legacy_uid']:x for x in json.loads(visual.read_text(encoding='utf-8'))}}
    policy_file=root/'review/v24-native-order-policy.json'
    need(sha(policy_file)==NATIVE_ORDER_POLICY_SHA256,'native-order policy SHA changed')
    policy=json.loads(policy_file.read_text(encoding='utf-8'))
    original=access.locate(policy['annotation_path'],policy['annotation_sha256'])
    need(sha(original)==policy['annotation_sha256'],'native annotation source changed')
    pages=json.loads(original.read_text(encoding='utf-8'))
    s['native_policy']={x['sample_uid']:x for x in policy['pages']}
    s['native_pages']={x['page_info']['image_path']:x for x in pages}
    s['native_annotation_file']=(str(original),policy['annotation_sha256'])
    _STATES[key]=s;return s

def admit(root,row,gold,record,cache=None):
    s=state(root);cache={} if cache is None else cache;uid=row['sample_uid'];bi,bg,br=s['before']
    need(uid in bi and uid in bg and uid in br,'unknown ancestor UID')
    before={'input':bi[uid],'gold':bg[uid],'record':br[uid]};after={'input':row,'gold':gold,'record':record}
    if uid in s['curation']:
        event=s['curation'][uid]
        need(event['before']==before and event['before_sha256']==digest(before),'ancestor transfer binding changed')
        need(event['after']==after and event['after_sha256']==digest(after),'new transfer binding changed')
        need(event['human_verified'] is False and event['grants_new_source_verification'] is False,'invented provenance upgrade')
        if 'FRESH_ACTIVE_HANDWRITING_VISUAL_AND_PLAIN_TEXT_CONTRACT' in event['operations']:
            visual=s['visual'].get(row['legacy_uid']);need(visual is not None and not visual['unresolved'],'no resolved fresh visual receipt')
            need(gold['reference_text']==visual['reference_text'],'reference differs from actual focused review')
            need(visual['source_image_sha256']==record['source_binding']['image_sha256'],'focused source changed')
            need(visual['core_exact_source_pixels'] is True,'focused core source equivalence absent')
            if row['protocol']=='PAIRED_INTERFERENCE':
                transfer=next((x for x in visual['pair_target_transfer'] if x['sample_uid']==uid),None)
                need(transfer is not None and transfer['image_sha256']==row['image_sha256'] and all(c['core_source_target_pixels_equal'] for c in transfer['checks']),'focused pair target transfer invalid')
            else:need(visual['core_uid']==uid and visual['core_image_sha256']==row['image_sha256'],'focused core identity changed')
    else:need(after==before,'unapproved change outside closed curation catalogue')
    if uid in s['native_policy']:
        entry=s['native_policy'][uid]
        validate_native_order(row,gold,bi[uid],bg[uid],entry,s['native_pages'][entry['native_page_image']])
        native_file,native_sha=s['native_annotation_file']
        files=cache.setdefault('files',{})
        need(native_file not in files or files[native_file]==native_sha,'native source inconsistent dependency hash')
        files[native_file]=native_sha
    need(bg[uid]['eligible'] is True and gold['eligible'] is True,'curation cannot create previously unadmitted samples')
    verified=cache.setdefault('v24_verified_ancestors',set())
    if uid not in verified:
        if bg[uid]['review_status']=='SOURCE_VERIFIED' or br[uid].get('resolution_admitted'):
            validate=importlib.import_module('legacy_v23.source_admission').validate_source_proof
        else:validate=importlib.import_module('legacy_v23.final_visual_admission').validate_visual
        validate(s['parent_root'],bi[uid],bg[uid],br[uid],cache)
        verified.add(uid)
    return s['curation'].get(uid,{'kind':'unchanged_exact_ancestor','sample_uid':uid})
