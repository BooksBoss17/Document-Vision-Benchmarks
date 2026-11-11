"""Validate the v1.1 current layer and explicit privacy-derived baseline; requires Pillow/pypdf."""
import argparse,importlib.util,json,sys,collections
from pathlib import Path
from core import *
from privacy_validation import verify_lineage,validate_derivative_baseline
def verify_checksums(root):
    listed={}
    for s in (root/'checksums.sha256').read_text(encoding='utf8').splitlines():
        h,n=s.split('  ',1);need(n not in listed and n!='checksums.sha256','checksum duplicate/self');need(sha(path(root,n))==h,'checksum mismatch: '+n);listed[n]=h
    # Nested baseline checksum is a normal file in the outer manifest.
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p!=root/'checksums.sha256'}
    need(set(listed)==actual,'checksum file set differs');return len(listed)
def schema_check(v,s):
    types={'object':dict,'array':list,'string':str,'integer':int,'boolean':bool,'number':float,'null':type(None)}
    expected=s.get('type');expected=[expected] if isinstance(expected,str) else expected
    if expected:need(any(type(v) is types[t] for t in expected),'schema type differs')
    if 'enum' in s:need(v in s['enum'],'schema enum differs')
    if isinstance(v,dict):
        need(set(s.get('required',[]))<=set(v),'schema missing field')
        if s.get('additionalProperties') is False:need(set(v)<=set(s.get('properties',{})),'schema extra field')
        for k,x in v.items():
            if k in s.get('properties',{}):schema_check(x,s['properties'][k])
    if isinstance(v,list):
        for x in v:schema_check(x,s.get('items',{}))
def validate_current(root):
    data=derive(root)
    for name,expected in data.items():
        got=rows(root/'standard'/(name+'.jsonl'));need(got==expected,'current derivation differs: '+name)
        schema=read(root/'schemas'/(name+'.schema.json'))
        for x in got:schema_check(x,schema)
    desc=read(root/'dataset.json');need(desc['version']==VERSION and desc['epoch']==EPOCH,'descriptor version/epoch differs')
    for field,name in [('active_gold','gold'),('active_model_inputs','model-inputs'),('default_gold','default-gold'),('default_model_inputs','default-model-inputs'),('pending','pending'),('held','held'),('source_coverage','source-coverage')]:
        need(desc[field]=='standard/'+name+'.jsonl','descriptor entry differs')
    need(desc['active_eligible_samples']==len(data['gold']) and desc['default_eligible_samples']==len(data['default-gold']) and desc['pending_samples']==len(data['pending']) and desc['held_samples']==len(data['held']),'descriptor counts differ')
    need(desc['human_verified'] is False and desc['baseline_checksums_sha256']==BASELINE_SHA,'descriptor authority differs')
    need(read(root/'quality/current.json')==quality(data),'quality report differs')
    sets=[set(index(data[k])) for k in ['gold','pending','held']];S=index(rows(root/'baseline/samples/samples.jsonl'))
    need(all(not sets[i]&sets[j] for i in range(3) for j in range(i)) and set.union(*sets)==set(S),'population partition differs')
    for k in ['gold','pending','held']:
        for g in data[k]:
            need(g['eligible_for_scoring'] is (k=='gold') and g['human_verified'] is False,'status flags differ')
            need(g['format'] in FORMATS,'format outside enum')
            if k=='gold':need(g['transcription'] and not g['uncertain_spans'],'unresolved gold')
    # Require real pixels, never silently downgrade this check.
    from PIL import Image
    for sid,s in S.items():
        p=path(root,'baseline/'+s['image']);need(sha(p)==s['image_sha256'],'image content hash differs')
        if sid!='sample-'+s['image_sha256'][:20]:verify_lineage(root,s)
        with Image.open(p) as im:
            need(im.size==(s['width'],s['height']),'image dimensions differ')
            need(hashlib.sha256(str(list(im.size)).encode()+im.convert('RGBA').tobytes()).hexdigest()==s['rgba_pixel_sha256'],'image pixel hash differs')
        for b in s['source_bindings']:
            if b.get('source_page_image'):need(sha(path(root,'baseline/'+b['source_page_image']))==b['source_page_sha256'],'source page hash differs')
    from pypdf import PdfReader
    sources=rows(root/'baseline/sources/manifest.jsonl');pages=index(rows(root/'sources/page-counts.jsonl'),'source_id')
    need(rows(root/'sources/manifest.jsonl')==rebase(sources),'current source manifest differs')
    for filename,schema_name in [('sources/manifest.jsonl','source-manifest'),('sources/page-counts.jsonl','page-counts'),('review/safe-accepted.jsonl','safe-accepted'),('review/patches.jsonl','patches')]:
        for x in rows(root/filename):schema_check(x,read(root/'schemas'/(schema_name+'.schema.json')))
    need(set(pages)=={s['source_id'] for s in sources if s['format']=='pdf'},'page count coverage differs')
    for s in sources:
        p=path(root,'baseline/'+s['relative_path']);need(sha(p)==s['sha256'],'source hash differs')
        if s['format']=='pdf':
            page=pages[s['source_id']];need(page['source_sha256']==s['sha256'] and page['source_file']=='baseline/'+s['relative_path'] and page['page_index_base']==0,'page metadata source binding differs')
            need(page['page_count']==len(PdfReader(p).pages),'page count differs')
    for s in S.values():
        for b in s['source_bindings']:
            if b['source_id'] in pages:need(type(b.get('page_index')) is int and 0<=b['page_index']<pages[b['source_id']]['page_count'],'source page index outside range')
    # Every primary compiled item must equal the published raw model result.
    for phase,compiled in [('blind','blind-results'),('comparison','comparison-results')]:
        raw={}
        for p in (root/'baseline/review/independent/evidence'/phase/'results').glob('*.json'):
            if p.name.endswith('.meta.json'):continue
            for x in read(p)['items']:need(x['id'] not in raw,'raw primary duplicate');raw[x['id']]=x
        merged=index(rows(root/'baseline/review/independent'/(compiled+'.jsonl')))
        need(set(raw)==set(merged),'primary raw coverage differs')
        for sid,x in merged.items():need({k:v for k,v in x.items() if k!='sample_id'}==raw[sid],'primary merged/raw differs')
    return {'status':'PASS','version':VERSION,'current':quality(data)['counts'],'images_pixel_verified':len(S),'pdf_page_counts_verified':len(pages),'primary_merged_sanitized_derivative_consistent':True,'original_model_authentication_applies_to_edited_inputs':False,'human_verified':False,'full_text_certification':False}
def validate(root):
    need(sha(root/'baseline/checksums.sha256')==BASELINE_SHA,'wrong frozen baseline')
    count=verify_checksums(root)
    spec=importlib.util.spec_from_file_location('frozen_v10',root/'baseline/tools/validate_standard_dataset.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    baseline=validate_derivative_baseline(root);result=validate_current(root);result.update(checksummed_files=count,baseline=baseline);return result
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args()
    try:print(json.dumps(validate(a.root.resolve()),ensure_ascii=False))
    except Exception as e:print(json.dumps({'status':'FAIL','error':str(e)},ensure_ascii=False));sys.exit(1)
