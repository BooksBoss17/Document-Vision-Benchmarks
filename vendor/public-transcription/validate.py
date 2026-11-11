"""Validate v2.4 full package, curation, source authority and pair geometry."""
from pathlib import Path
import sys
sys.dont_write_bytecode=True
from collections import Counter,defaultdict
import argparse,json
import scoring as S
from curation_admission import state,admit,digest
from package_integrity import verify_package,sha,safe

def validate(root,decode=True):
    root=Path(root).resolve();errors=[]
    try:
        package=verify_package(root)
        ii,gg,rr=[S.uid_map(S.jsonl((root/f).read_bytes()),f) for f in ['inputs/model-inputs.jsonl','annotations/gold.jsonl','annotations/records.jsonl']]
        if not (list(ii)==list(gg)==list(rr)):raise ValueError('three inventories differ in UID or order')
        descriptor=S.decode_json((root/'dataset.json').read_bytes())
        S.validate_inputs(ii,root);S.validate_gold(gg,ii)
        st=state(root);bi,bg,br=st['before']
        if ii.keys()!=bi.keys():raise ValueError('v2.4 must retain full ancestor UID inventory')
        cache={'files':{}};pairs=defaultdict(list)
        for uid,i in ii.items():
            g,r=gg[uid],rr[uid]
            actual={'input':i,'gold':g,'record':r};original={'input':bi[uid],'gold':bg[uid],'record':br[uid]}
            e=st['curation'].get(uid)
            if e:
                if e['before']!=original or e['before_sha256']!=digest(original) or e['after']!=actual or e['after_sha256']!=digest(actual):errors.append(uid+': curation triple differs')
            elif actual!=original:errors.append(uid+': changed without fixed curation event')
            for k in ('sample_uid','legacy_uid','protocol','category_path','image','image_sha256','width','height','group_id','task','pair_id','pair_variant','metadata'):
                if r.get(k)!=i.get(k):errors.append(uid+': input/record differs '+k)
            for k in ('reference_text','reference_format','review_status','eligible','blocks','source_binding','unordered_region_ids'):
                if r.get(k)!=g.get(k):errors.append(uid+': gold/record differs '+k)
            if g['eligible']:
                try:admit(root,i,g,r,cache)
                except (OSError,ValueError,KeyError,TypeError) as exc:errors.append(uid+': admission '+str(exc))
            if g['reference_format']=='BLOCKS' and g.get('blocks') and g['reference_text']!=chr(10).join(b['text'] for b in g['blocks']):errors.append(uid+': PAGE aggregate not block order')
            if g['eligible'] and not bg[uid]['eligible']:errors.append(uid+': forbidden eligibility expansion')
            if i.get('pair_id'):pairs[i['pair_id']].append(i)
        counts=Counter(g['review_status'] for g in gg.values())
        visual={u for u,g in gg.items() if g['eligible'] and g['review_status'] in S.REVIEWED and not rr[u].get('resolution_admitted')}
        local={u for u,g in gg.items() if g['eligible'] and g['review_status']=='CORRECTED_REVIEWED' and rr[u].get('resolution_admitted')}
        source={u for u,g in gg.items() if g['eligible'] and g['review_status']=='SOURCE_VERIFIED'}
        for key,actual in [('total_samples',len(ii)),('eligible_samples',sum(g['eligible'] for g in gg.values())),('source_verified_samples',len(source)),('visual_reviewed_samples',len(visual)),('resolution_corrected_samples',len(local)),('core_samples',sum(i['protocol']!='PAIRED_INTERFERENCE' for i in ii.values())),('review_status_counts',dict(counts)),('by_protocol',dict(Counter(i['protocol'] for i in ii.values())))]:
            if descriptor.get(key)!=actual:errors.append('descriptor count differs: '+key)
        for name,expected in [('visual-reviewed-all',visual),('resolution-corrected-all',local),('source-verified-all',source)]:
            p=root/'selections'/(name+'.json')
            if p.exists() and set(S.decode_json(p.read_bytes())['sample_uids'])!=expected:errors.append('provenance selection differs: '+name)
        for p in (root/'selections').glob('*.json'):
            x=S.decode_json(p.read_bytes())
            if x['inputs_sha256']!=sha(root/'inputs/model-inputs.jsonl') or len(x['sample_uids'])!=len(set(x['sample_uids'])) or not set(x['sample_uids'])<=ii.keys():errors.append('invalid selection binding: '+p.name)
        expected_leaves=set()
        for cat in {r['category_path'] for r in rr.values()}:
            p=safe(root,cat+'/samples.jsonl');expected_leaves.add(p)
            leaf=S.uid_map(S.jsonl(p.read_bytes()),str(p));expected={u:r for u,r in rr.items() if r['category_path']==cat}
            if leaf!=expected:errors.append(cat+': leaf records differ')
        if {p.resolve() for p in root.rglob('samples.jsonl')}!=expected_leaves:errors.append('unexpected leaf inventory')
        sources={}
        for pid,members in pairs.items():
            if len(members)!=2:errors.append(pid+': pair cardinality');continue
            a,b=members
            if gg[a['sample_uid']]['eligible']!=gg[b['sample_uid']]['eligible'] or gg[a['sample_uid']]['reference_text']!=gg[b['sample_uid']]['reference_text']:errors.append(pid+': asymmetric pair reference/admission')
            try:st['geometry'].pair_geometry(root,members,rr,decode,sources)
            except (OSError,ValueError,KeyError,TypeError) as exc:errors.append(pid+': pair geometry '+str(exc))
        decoded=0
        if decode:
            from PIL import Image
            for p in root.rglob('*'):
                if p.suffix.lower() in {'.png','.jpg','.jpeg'} and p.is_file():
                    with Image.open(p) as image:image.load()
                    decoded+=1
        for p,h in cache['files'].items():
            if sha(p)!=h:errors.append('source dependency changed: '+p)
        st['access'].verify()
        return {'passed':not errors,'dataset_version':'2.4.0','samples':len(ii),'eligible':sum(g['eligible'] for g in gg.values()),'pairs':len(pairs),'decoded_images':decoded,'pair_pixel_validation':'FULL_SOURCE_RECONSTRUCTION' if decode else 'SOURCE_GEOMETRY_AND_HASH_ONLY','admission_counts':dict(counts),'curation_views':len(st['curation']),'full_visible_content_review_claimed':False,'human_verified':False,'package_integrity':package,'runtime_bindings':st['access'].frozen,'resolved_dependencies':st['access'].remapped,'errors':errors}
    except (OSError,ValueError,KeyError,TypeError,ImportError,AttributeError,RecursionError,OverflowError) as exc:
        return {'passed':False,'dataset_version':'2.4.0','error_type':type(exc).__name__,'errors':[str(exc)],'human_verified':False,'full_visible_content_review_claimed':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--skip-decode',action='store_true');a=p.parse_args()
    root=a.root.resolve();out=a.output.resolve()
    if out==root or out.is_relative_to(root) or out.exists():p.error('output must be a new file outside the package')
    result=validate(root,not a.skip_decode);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+chr(10),encoding='utf-8');print(json.dumps({'passed':result['passed'],'errors':len(result['errors']),'output':str(out)},ensure_ascii=False));return 0 if result['passed'] else 1
if __name__=='__main__':sys.exit(main())
