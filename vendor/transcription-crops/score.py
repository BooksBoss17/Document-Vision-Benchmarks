"""Seal prediction bytes before scoring. Exact and conservative lexical metrics, no semantic math equivalence."""
import argparse,datetime,re,sys,unicodedata
from pathlib import Path
from core import *
def prediction_rows(root,predictions,view):
    inputs=rows(root/'standard'/(view+'model-inputs.jsonl'));expected=index(inputs);xs=rows(predictions);got=index(xs)
    need(set(got)==set(expected),'prediction coverage differs (missing/extra IDs)')
    need(all(set(x)=={'sample_id','transcription'} and isinstance(x['transcription'],str) for x in xs),'prediction fields/types differ')
    return inputs,got
def lexical(s):
    s=unicodedata.normalize('NFC',s).replace('\r\n','\n').replace('\r','\n').strip()
    for left,right in [('$$','$$'),('\\[','\\]'),('\\(','\\)'),('$','$')]:
        if s.startswith(left) and s.endswith(right) and len(s)>len(left)+len(right):s=s[len(left):-len(right)].strip();break
    return re.sub(r'\s+',' ',s)
def distance(a,b):
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current=[i]
        for j,y in enumerate(b,1):current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
        previous=current
    return previous[-1]
def seal(root,predictions,view):
    xs,_=prediction_rows(root,predictions,view)
    return {'schema':'transcription-v11-prediction-seal-v1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'predictions_sha256':sha(predictions),'inputs_sha256':sha(root/'standard'/(view+'model-inputs.jsonl')),'protocol_sha256':sha(root/'SCORING.md'),'view':view,'count':len(xs),'claim':'Local byte seal; does not prove prior non-access to gold. Dataset historically exposed.'}
def score(root,predictions,view,receipt):
    inputs,pred=prediction_rows(root,predictions,view);s=read(receipt)
    need(s.get('schema')=='transcription-v11-prediction-seal-v1' and s['view']==view and s['count']==len(inputs),'seal identity differs')
    for k,p in [('predictions_sha256',predictions),('inputs_sha256',root/'standard'/(view+'model-inputs.jsonl')),('protocol_sha256',root/'SCORING.md')]:need(s[k]==sha(p),'prediction seal changed: '+k)
    # Gold is read only after the complete prediction/selection/protocol seal is checked.
    gold=index(rows(root/'standard'/(view+'gold.jsonl')));need(set(gold)==set(pred),'gold selection differs')
    results=[];groups={}
    for inp in inputs:
        sid=inp['sample_id'];g=gold[sid];answer=pred[sid]['transcription'];ref=lexical(g['transcription']);hyp=lexical(answer)
        result={'sample_id':sid,'exact':answer==g['transcription'],'lexical_exact':hyp==ref,'edit_distance':distance(ref,hyp),'reference_characters':len(ref),'format':g['format']};results.append(result)
        groups.setdefault(g['format'],[]).append(result)
    def summary(xs):
        chars=sum(x['reference_characters'] for x in xs);errors=sum(x['edit_distance'] for x in xs)
        return {'samples':len(xs),'exact_matches':sum(x['exact'] for x in xs),'lexical_matches':sum(x['lexical_exact'] for x in xs),'lexical_exact_rate':sum(x['lexical_exact'] for x in xs)/len(xs),'edit_distance':errors,'reference_characters':chars,'cer':errors/chars if chars else None}
    return {'version':VERSION,'view':view,'summary':summary(results),'by_format':{k:summary(v) for k,v in groups.items()},'items':results,'human_verified':False,'metric_scope':'String/lexical transcription only. No formula semantic equivalence, full-document accuracy, or hidden-holdout claim.','seal_sha256':sha(receipt),'gold_sha256':sha(root/'standard'/(view+'gold.jsonl'))}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['seal','score']);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--predictions',type=Path,required=True);p.add_argument('--seal',type=Path,required=True);p.add_argument('--output',type=Path);p.add_argument('--view',choices=['default','full'],default='default');a=p.parse_args();view='default-' if a.view=='default' else ''
    try:
        if a.action=='seal':need(not a.seal.exists(),'seal already exists');result=seal(a.root,a.predictions,view);write(a.seal,result)
        else:result=score(a.root,a.predictions,view,a.seal)
        if a.output:write(a.output,result)
        print(json.dumps(result if a.action=='seal' else {k:v for k,v in result.items() if k!='items'},ensure_ascii=False))
    except Exception as e:print(json.dumps({'status':'FAIL','error':str(e)},ensure_ascii=False));sys.exit(1)
