"""Prepare immutable answer-free OCR requests; bind returned predictions separately.

Preparation verifies public input hashes but never parses gold, records or review evidence. The model runner
consumes requests.jsonl and emits predictions in the documented v2 schema.
"""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import re
import sys
from package_integrity import verify_package

def load_json(raw):
    def unique(pairs):
        value={}
        for key,child in pairs:
            if key in value:raise ValueError('duplicate JSON key '+key)
            value[key]=child
        return value
    return json.loads(raw,object_pairs_hook=unique)

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()

def write(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def answer_fields(value):
    forbidden={'reference_text','answer','gold','expected_answer','expected_text','eligible','review_status',
               'review_evidence','source_evidence','source_admission','reference_sha256','review_binding'}
    if isinstance(value,dict):
        for key,child in value.items():
            if key in forbidden:
                yield key
            yield from answer_fields(child)
    elif isinstance(value,list):
        for child in value:
            yield from answer_fields(child)


def prepare(root, output, model_id, fingerprint, config, selection=None):
    root=Path(root).resolve();output=Path(output).resolve();config=Path(config).resolve()
    if output==root or root in output.parents:
        raise ValueError('preparation output must stay outside dataset root')
    if not model_id or not re.fullmatch('[0-9a-fA-F]{64}',fingerprint):
        raise ValueError('model ID and actual model fingerprint required')
    verify_package(root, public_only=True)
    input_path=root/'inputs/model-inputs.jsonl'
    raw=input_path.read_bytes();input_hash=hashlib.sha256(raw).hexdigest()
    rows=[load_json(line) for line in raw.decode('utf-8-sig').splitlines() if line.strip()]
    index={}
    for r in rows:
        uid=r.get('sample_uid')
        if not isinstance(uid,str) or not uid or uid in index:
            raise ValueError('invalid/duplicate input UID')
        if any(answer_fields(r)):
            raise ValueError('answers/review decisions forbidden in model inputs')
        index[uid]=r
    selected=set(index)
    selection_bytes=None
    if selection in {'admitted-all','reviewed-all'}:
        selection=root/'selections/admitted-all.json'
    elif selection is None and (root/'selections/admitted-all.json').exists():
        selection=root/'selections/admitted-all.json'
    if selection:
        selection_bytes=Path(selection).read_bytes();s=load_json(selection_bytes)
        uids=s.get('sample_uids',[])
        if s.get('schema_version')!=2 or s.get('inputs_sha256')!=input_hash:
            raise ValueError('selection input binding mismatch')
        if not isinstance(uids,list) or not uids or len(uids)!=len(set(uids)) or not set(uids)<=set(index):
            raise ValueError('selection invalid/duplicate/unknown UID')
        selected=set(uids)
    config_raw=config.read_bytes();load_json(config_raw)
    requests=[]
    image_hashes={}
    for uid in sorted(selected):
        r=index[uid];rel=r['image'];p=(root/rel).resolve()
        if Path(rel).is_absolute() or '..' in Path(rel).parts or not p.is_relative_to(root):
            raise ValueError('image path escape')
        if rel not in image_hashes:
            image_hashes[rel]=sha(p)
        actual=image_hashes[rel]
        if actual!=r['image_sha256']:
            raise ValueError('image changed '+uid)
        task=r['task']
        if task.get('support_status')=='UNSUPPORTED_TASK':
            raise ValueError('UNSUPPORTED_TASK '+uid)
        policy=task.get('table_serialization_policy')
        if policy is not None and not (policy=='TABLE_LAYOUT_V1' and task.get('output_format')=='TABLE_HTML' and policy in task.get('instruction','')):
            raise ValueError('unknown/incompatible table policy '+uid)
        if task.get('annotation_policy') not in (None,'CASIA_NATIVE_LABELLED_TEXT_V1'):
            raise ValueError('unknown annotation policy '+uid)
        profile=task.get('serialization_profile')
        if profile is not None and (profile!='UPSTREAM_TEX_MIXED_V1' or not (task.get('output_format')=='TEXT' or
                (r['protocol']=='PAGE_TRANSCRIPTION' and task.get('output_format')=='PAGE_BLOCKS' and
                 isinstance(task.get('region_formats'),dict) and 'TEXT' in task['region_formats'].values()))):
            raise ValueError('unknown/incompatible serialization profile '+uid)
        scope=task.get('scope')
        if scope is not None and not ((r['protocol']=='PAGE_TRANSCRIPTION' and scope in {'ANNOTATED_REGIONS','FULL_VISIBLE_CONTENT'}) or (r['protocol']!='PAGE_TRANSCRIPTION' and scope in {'SOURCE_ANNOTATED_REGION','UPSTREAM_TARGET_REFERENCE'})):
            raise ValueError('unknown/incompatible task scope '+uid)
        request={'sample_uid':uid,'image_path':str(p),'image_sha256':actual,
                 'protocol':r['protocol'],'instruction':task['instruction'],
                 'output_format':task['output_format'],
                 'target_regions':task.get('target_regions',[])}
        for field in ('scope','serialization_profile'):
            if field in task:
                request[field]=task[field]
        regions=request['target_regions']
        if (not isinstance(regions,list) or not all(isinstance(x,dict) and isinstance(x.get('region_id'),str) and x['region_id'] for x in regions)
                or len({x['region_id'] for x in regions})!=len(regions)):
            raise ValueError('invalid/duplicate target region ID')
        if any(set(x)-{'region_id','bbox','polygon'} for x in request['target_regions']):
            raise ValueError('region fields leak expected answer/order')
        if r['protocol']=='PAGE_TRANSCRIPTION':
            ids={x['region_id'] for x in request['target_regions']}
            if 'unordered_region_ids' in task:
                unordered=task['unordered_region_ids']
                if (not isinstance(unordered,list) or not all(isinstance(x,str) for x in unordered)
                        or len(unordered)!=len(set(unordered)) or not set(unordered)<=ids or 'region_formats' not in task):
                    raise ValueError('invalid PAGE unordered region IDs/format map')
                request['unordered_region_ids']=unordered
            if 'region_formats' in task:
                formats=task['region_formats']
                if not isinstance(formats,dict) or set(formats)!=ids or not all(isinstance(v,str) and v in {'TEXT','LATEX','TABLE_HTML'} for v in formats.values()):
                    raise ValueError('invalid PAGE region_formats')
                request['region_formats']=formats
        elif {'region_formats','unordered_region_ids'} & task.keys():
            raise ValueError('PAGE-only scope fields')
        requests.append(request)
    if sha(input_path)!=input_hash:
        raise ValueError('inputs changed while preparing')
    output.mkdir(parents=True,exist_ok=False)
    (output/'inputs.jsonl').write_bytes(raw)
    (output/'config.json').write_bytes(config_raw)
    if selection_bytes is not None:
        (output/'selection.json').write_bytes(selection_bytes)
    with (output/'requests.jsonl').open('w',encoding='utf-8') as f:
        for r in requests:
            f.write(json.dumps(r,ensure_ascii=False)+'\n')
    receipt={'schema_version':2,'dataset_version':'2.4.0','dataset_root':str(root),
             'model':{'id':model_id,'fingerprint':fingerprint.lower()},'inputs_sha256':input_hash,
             'config_sha256':hashlib.sha256(config_raw).hexdigest(),
             'selection_sha256':hashlib.sha256(selection_bytes).hexdigest() if selection_bytes is not None else None,
             'requests_sha256':sha(output/'requests.jsonl'),'prepared_at':now(),
             'selected_count':len(requests),'model_execution_attested':False}
    write(output/'prepared.json',receipt)
    return receipt

def bind_predictions(prepared_dir, predictions):
    directory=Path(prepared_dir).resolve();p=directory/'run-manifest.json'
    if p.exists():
        raise FileExistsError('run-manifest already frozen')
    r=json.loads((directory/'prepared.json').read_text('utf-8'))
    for name,field in [('inputs.jsonl','inputs_sha256'),('config.json','config_sha256'),('requests.jsonl','requests_sha256')]:
        if sha(directory/name)!=r[field]:
            raise ValueError('prepared artifact drift: '+name)
    if r['selection_sha256'] is not None and sha(directory/'selection.json')!=r['selection_sha256']:
        raise ValueError('selection drift')
    pred_bytes=Path(predictions).read_bytes()
    target=directory/'predictions.jsonl'
    with target.open('xb') as f:
        f.write(pred_bytes)
    manifest={k:r[k] for k in ('schema_version','dataset_version','model','config_sha256','inputs_sha256','selection_sha256','requests_sha256')}
    manifest.update(predictions_sha256=hashlib.sha256(pred_bytes).hexdigest(),created_at=now(),
                    provenance_note='Binds caller model identity and exact files; does not attest engine execution.')
    with p.open('x',encoding='utf-8') as f:
        json.dump(manifest,f,ensure_ascii=False,indent=2)
    return manifest

def _main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('--root',required=True);p.add_argument('--output',required=True)
    p.add_argument('--model-id',required=True);p.add_argument('--model-fingerprint',required=True)
    p.add_argument('--config',required=True);p.add_argument('--selection',help='default: selections/admitted-all.json when present; reviewed-all compatibility alias')
    p=sub.add_parser('bind');p.add_argument('--prepared',required=True);p.add_argument('--predictions',required=True)
    a=ap.parse_args()
    result=prepare(a.root,a.output,a.model_id,a.model_fingerprint,a.config,a.selection) if a.command=='prepare' else bind_predictions(a.prepared,a.predictions)
    print(json.dumps(result,ensure_ascii=False,indent=2))
def main():
    try:
        _main()
        return 0
    except (OSError,ValueError,KeyError,TypeError,AttributeError,RecursionError,OverflowError) as exc:
        print(json.dumps(dict(passed=False,error_type=type(exc).__name__,error=str(exc)),ensure_ascii=False),file=sys.stderr)
        return 2
if __name__=='__main__':
    sys.exit(main())
