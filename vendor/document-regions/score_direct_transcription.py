"""Stdlib-only crop scorer: validate before freezing, explicit coverage and CER scopes."""
import sys
sys.dont_write_bytecode=True
import argparse,shutil,datetime,collections
from package_io import *
from recipe import MODEL_KEYS,verify_exports,VERSION
from text_metrics import narrow,distance
def prepare_inputs(root,view):
    d=read(root/'dataset.json');need(d['version']==VERSION,'DATASET_VERSION')
    inputkey='default_model_inputs' if view=='default' else 'model_inputs';goldkey='default_gold' if view=='default' else 'active_gold'
    p=safe(root,d[inputkey]);safe(root,d[goldkey]);inputs=rows(p);expected=index(inputs);need(inputs,'EMPTY_SCORE_VIEW')
    for m in inputs:need(set(m)==MODEL_KEYS and m['input_fingerprint']==objsha({k:v for k,v in m.items() if k!='input_fingerprint'}),'MODEL_INPUT_SCHEMA_OR_FINGERPRINT')
    return d,inputs,expected,inputkey,goldkey
def predictions_for(predictions,expected):
    xs=rows(predictions);got=index(xs)
    need(set(got)<=set(expected),'EXTRA_PREDICTION_ID')
    for uid,p in got.items():
        need(set(p)=={'sample_uid','input_fingerprint','status','text'},'PREDICTION_FIELDS')
        need(p['input_fingerprint']==expected[uid]['input_fingerprint'],'PREDICTION_WRONG_INPUT_FINGERPRINT')
        need(p['status'] in {'SUCCESS','REVIEW','FAILED'} and isinstance(p['text'],str) and len(p['text'])<=6000,'PREDICTION_SCHEMA_OR_LENGTH')
    return got
def measure(gold,expected,predicted):
    gb=index(gold);need(set(gb)==set(expected) and len(gold)==len(expected),'INPUT_GOLD_SCOPE_MISMATCH')
    items=[]
    for uid,g in gb.items():
        p=predicted.get(uid);state=p['status'] if p else 'MISSING';ok=state=='SUCCESS';hyp=p['text'] if ok else '';ref=narrow(g['reference_text']);nh=narrow(hyp);edits=distance(ref,nh)
        items.append({'sample_uid':uid,'status':state,'measurable':ok,'raw_match':ok and p['text']==g['reference_text'],'literal_match':ok and nh==ref,'literal_edits':edits if ok else None,'reference_characters':len(ref),'full_view_empty_output_penalty_edits':edits,'reference_format':g['reference_format']})
    def summarize(xs):
        n=len(xs);measured=[x for x in xs if x['measurable']];chars=sum(x['reference_characters'] for x in measured);allchars=sum(x['reference_characters'] for x in xs);states=dict(collections.Counter(x['status'] for x in xs));correct=sum(x['literal_match'] for x in xs)
        return {'samples':n,'state_counts':{k:states.get(k,0) for k in ['SUCCESS','REVIEW','FAILED','MISSING']},'literal_correct':correct,'literal_incorrect_success':len(measured)-correct,'raw_correct':sum(x['raw_match'] for x in xs),'literal_match_rate_over_full_view':correct/n if n else None,'literal_measured':len(measured),'coverage':len(measured)/n if n else None,'measured_reference_characters':chars,'full_view_reference_characters':allchars,'CER_measured_only':sum(x['literal_edits'] for x in measured)/chars if chars else None,'CER_full_view_empty_output_penalty':sum(x['full_view_empty_output_penalty_edits'] for x in xs)/allchars if allchars else None}
    formats={}
    for x in items:formats.setdefault(x['reference_format'],[]).append(x)
    return items,summarize(items),{k:summarize(v) for k,v in formats.items()}
def main(root,predictions,output,view):
    root=root.resolve();output=output.resolve();predictions=predictions.resolve()
    need(not output.is_relative_to(root) and not output.exists(),'FRESH_EXTERNAL_SCORE_OUTPUT_REQUIRED')
    d,inputs,expected,inputkey,goldkey=prepare_inputs(root,view);predictions_for(predictions,expected)
    # Byte verification reads no gold JSON content before prediction sealing.
    verify_manifest(root);before=sha(predictions);output.mkdir(parents=True);frozen=output/'predictions-frozen.jsonl';shutil.copy2(predictions,frozen);lines(output/'inputs-frozen.jsonl',inputs);need(sha(frozen)==before==sha(predictions),'PREDICTIONS_CHANGED_DURING_FREEZE')
    seal={'prediction_sha256':sha(frozen),'input_manifest_sha256':sha(safe(root,d[inputkey])),'input_objects_sha256':objsha(inputs),'dataset_version':VERSION,'view':view,'expected_inputs':len(expected),'gold_read_before_freeze':False,'protocol':'NARROW_TOKEN_SAFE_V2','protocol_sha256':sha(root/'SCORING.md'),'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()};write(output/'prediction-seal.json',seal)
    data=verify_exports(root);gold=rows(safe(root,d[goldkey]));need(gold==data[d[goldkey]],'SELECTED_GOLD_DERIVATION_DIFFERS');pred=predictions_for(frozen,expected);items,summary,byformat=measure(gold,expected,pred)
    result={**seal,'status':'DIRECT_CROP_LITERAL_SCORE_COMPLETE','gold_sha256':sha(safe(root,d[goldkey])),**summary,'by_format':byformat,'semantic_accuracy_or_math_equivalence_certified':False,'human_verified':False,'historically_exposed':True,'full_view_penalty_policy':'REVIEW/FAILED/MISSING imputed empty only for explicitly named penalty metric; measured CER excludes them.'}
    lines(output/'items.jsonl',items);write(output/'summary.json',result);return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--predictions',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--view',choices=['default','scorable'],default='default');a=p.parse_args()
    try:print(json.dumps(main(a.root,a.predictions,a.output,a.view),ensure_ascii=False))
    except Exception as e:print(json.dumps({'status':'FAIL','error':str(e)},ensure_ascii=False));sys.exit(1)
