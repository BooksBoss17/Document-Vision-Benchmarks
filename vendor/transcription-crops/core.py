"""Portable v1.1 derivation. Pure derivation; explicitly labeled privacy-derived baseline plus patches."""
import copy,hashlib,json,unicodedata,collections
from pathlib import Path
VERSION='1.1.1'
EPOCH='PRIVACY_20261111'
BASELINE_SHA='a72c70616c035da2237cbbfffd6280c286c326d388c5c43ee6f28a783a860787'
FORMATS={'TEXT','LATEX','TEXT_WITH_LATEX','SYMBOL','NONE'}
def need(ok,msg):
    if not ok: raise ValueError(msg)
def read(p): return json.loads(Path(p).read_text(encoding='utf8'))
def rows(p): return [json.loads(s) for s in Path(p).read_text(encoding='utf8').splitlines() if s.strip()]
def write(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def lines(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in v),encoding='utf8')
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def objsha(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def index(xs,key='sample_id'):
    out={}
    for x in xs:need(x[key] not in out,'duplicate '+key);out[x[key]]=x
    return out
def path(root,s):
    need(isinstance(s,str) and s and ':' not in s and '\\' not in s and not s.startswith('/') and '..' not in Path(s).parts,'nonportable path')
    p=(root/s).resolve();need(p.is_relative_to(root.resolve()) and p.is_file(),'missing or escaping path: '+s);return p
def rebase(v):
    if isinstance(v,list):return [rebase(x) for x in v]
    if not isinstance(v,dict):return v
    out={}
    for k,x in v.items():
        if k in {'image','source_file','source_page_image','relative_path'} and isinstance(x,str) and x and not ':' in x:
            out[k]='baseline/'+x
        else:out[k]=rebase(x)
    return out
def derive(root):
    base=root/'baseline';oldg=rows(base/'standard/gold.jsonl');oldp=rows(base/'standard/pending.jsonl')
    oldnear=rows(base/'review/extras/near-pairs.jsonl');blind=index(rows(base/'review/independent/blind-results.jsonl'));comp=index(rows(base/'review/independent/comparison-results.jsonl'))
    gold=copy.deepcopy(oldg);pending=copy.deepcopy(oldp);near=copy.deepcopy(oldnear)
    collections_by_file={'gold':index(gold),'pending':index(pending),'near-pairs':index(near,'pair_id')}
    patches=rows(root/'review/patches.jsonl');seen=set()
    for p in patches:
        ident=(p['target'],p['id']);need(ident not in seen,'duplicate patch');seen.add(ident)
        need(p['target'] in collections_by_file,'unknown patch target');item=collections_by_file[p['target']][p['id']]
        need(objsha(item)==p['before_sha256'],'patch before fingerprint differs')
        need(set(p['set'])<=({'transcription','reason','uncertain_spans'} if p['target'] in {'gold','pending'} else {'decision','evidence'}),'patch changes scoring population or protected fields')
        need(p['evidence'] and p['rationale'],'patch missing rationale')
        for evidence in p['evidence']:path(root,evidence)
        item.update(p['set'])
    safe=index(rows(root/'review/safe-accepted.jsonl'));need(set(safe)=={x['sample_id'] for x in oldg}-set(comp),'safe-accepted coverage differs')
    origin=read(root/'review/safe-accepted-origin.json');need(origin['sha256']==sha(root/'review/safe-accepted.jsonl') and origin['count']==len(safe),'safe-accepted origin differs')
    for filename,key in [('batches.json','batches_sha256'),('schema.json','schema_sha256'),('policy.txt','policy_sha256')]:need(sha(base/'review/independent/evidence/comparison'/filename)==origin['protocol_fingerprints'][key],'safe-accepted protocol binding differs')
    for sid,x in safe.items():
        old=index(oldg)[sid];b={k:v for k,v in blind[sid].items() if k!='sample_id'}
        need(x['blind']==b and x['final_transcription']==old['transcription'] and x['format']==old['format'] and x['acceptance']=='SAFE_LITERAL_EQUIVALENCE','safe-accepted evidence differs')
        records=index(rows(base/'samples/records.jsonl'),'record_id')
        for a in x['old_answers']:
            need(records[a['record_id']]['old_answer']==a['answer'],'safe legacy answer differs')
    for xs in [gold,pending]:
        for x in xs:
            sid=x['sample_id'];b=blind[sid]
            stage='FINAL_SCREEN_BLIND_AND_COMPARISON' if x.get('review_epoch') else ('BLIND_AND_LITERAL_EQUIVALENCE' if sid in safe else 'BLIND_AND_COMPARISON')
            if ('gold',sid) in seen:stage='VISUAL_PATCH_OF_REVIEWED_BASELINE'
            if ('pending',sid) in seen:stage='PARTIAL_VISUAL_PATCH_WHOLE_ITEM_PENDING'
            x['review_stage']=stage;x['review_method']=stage;x.setdefault('review_epoch','INDEPENDENT_BASELINE_20261001');x['export_epoch']=EPOCH
            x['baseline_crop_integrity']=b['crop_integrity'];x['baseline_legibility']=b['legibility']
            x['review_limit']='FULL_ITEM_REVIEW_REQUIRED' if sid in index(pending) else 'AI_REFERENCE_NOT_HUMAN_CERTIFIED'
    gold,pending=map(rebase,[gold,pending]);G=index(gold);P=index(pending)
    groups=[];N=index(near,'pair_id');S=index(rows(base/'samples/samples.jsonl'));omitted=set()
    for original in rows(base/'standard/variant-groups.jsonl'):
        if not all(N[p]['decision']=='SAME_VISIBLE_CONTENT_AND_LAYOUT' for p in original['pair_ids']):continue
        g=copy.deepcopy(original);members=set(g['sample_ids']);eligible=members&set(G)
        can=len(eligible)>=2 and eligible==members and len({(G[s]['format'],unicodedata.normalize('NFC',G[s]['transcription']).strip()) for s in eligible})==1
        keep=sorted(eligible,key=lambda s:(-S[s]['width']*S[s]['height'],s))[0] if can else None
        removed=eligible-{keep} if can else set();omitted|=removed
        g.update(default_merge_allowed=can,default_view_representative=keep,default_view_omitted_variant_ids=sorted(removed));groups.append(g)
    def inputs(xs):return [{'sample_id':x['sample_id'],'image':x['image'],'image_sha256':x['image_sha256'],'width':S[x['sample_id']]['width'],'height':S[x['sample_id']]['height'],'requested_format':x['format']} for x in xs]
    default=[x for x in gold if x['sample_id'] not in omitted]
    held=rebase(rows(base/'review/held/held-audit.jsonl'));coverage=[]
    for c in rows(base/'standard/source-coverage.jsonl'):
        c=copy.deepcopy(c);sid=c['source_id'];bound={s for s,v in S.items() if any(b['source_id']==sid for b in v['source_bindings'])}
        c.update(eligible_samples=len(bound&set(G)),pending_primary_samples=len(bound&set(P)),coverage_epoch=EPOCH,count_unit='UNIQUE_SAMPLE_SOURCE_BINDING_PAIR');coverage.append(rebase(c))
    return {'gold':gold,'pending':pending,'held':held,'model-inputs':inputs(gold),'default-gold':default,'default-model-inputs':inputs(default),'near-pairs':rebase(near),'variant-groups':groups,'source-coverage':coverage}
def quality(data):
    g=data['gold'];h=data['held'];near=data['near-pairs']
    return {'version':VERSION,'epoch':EPOCH,'counts':{k:len(data[k]) for k in ['gold','default-gold','pending','held','near-pairs','variant-groups']},'decision_histogram':dict(collections.Counter(x['decision'] for x in g)),'review_stage_histogram':dict(collections.Counter(x['review_stage'] for x in g)),'near_decisions':dict(collections.Counter(x['decision'] for x in near)),'blocked_variant_groups':sum(not x['default_merge_allowed'] for x in data['variant-groups']),'held_decisions':dict(collections.Counter(x['decision'] for x in h)),'human_verified':False,'whole_population_visual_recertified':False,'new_model_calls':0,'new_model_calls_scope':'REFERENCE_GENERATION_ONLY','new_reference_generation_model_calls':0,'privacy_ocr_inference_used':True,'quality_scope':'Privacy-derived images and labels, with unchanged membership and inherited AI reference judgments. Privacy-audit OCR is separate from gold review/performance evaluation and gives no human/full-text certification; original sealed proofs do not certify edited inputs.'}
