"""Unified v1.2 CLI:verify,prepare,bind,score. No implicit model calls."""
from pathlib import Path
import argparse,json,sys
from core import PROTOCOLS,validate,integrity,outside,write
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['verify','prepare','bind','score']);parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--output',type=Path);parser.add_argument('--decode',action='store_true');parser.add_argument('--protocol',choices=PROTOCOLS,default=PROTOCOLS[0]);parser.add_argument('--selection',type=Path);parser.add_argument('--model-id');parser.add_argument('--model-fingerprint');parser.add_argument('--config',type=Path);parser.add_argument('--prepared',type=Path);parser.add_argument('--predictions',type=Path);parser.add_argument('--run-manifest',type=Path);a=parser.parse_args()
 try:
  from core import need
  if a.action=='verify':
   result=validate(a.root,a.decode)
   if a.output:out=outside(a.root,a.output);out.parent.mkdir(parents=True,exist_ok=True);write(out,result)
  elif a.action=='prepare':
   need(a.output is not None and a.config is not None,'prepare requires output and config')
   from workflow import prepare
   result=prepare(a.root,a.output,a.protocol,a.selection,a.model_id,a.model_fingerprint,a.config)
  elif a.action=='bind':
   need(a.prepared is not None and a.predictions is not None,'bind requires prepared and predictions');integrity(a.root)
   outside(a.root,a.prepared)
   from workflow import bind
   result=bind(a.prepared,a.predictions)
  else:
   need(a.output is not None and a.predictions is not None and a.run_manifest is not None,'score requires output,predictions,run-manifest')
   from workflow import score
   result=score(a.root,a.output,a.selection,a.predictions,a.run_manifest)
  summary=result if a.action!='score' else {k:result[k] for k in ['passed','dataset_version','protocol','scope','selected_views']};print(json.dumps(summary,ensure_ascii=False,allow_nan=False));return 0
 except (OSError,ValueError,KeyError,TypeError,AttributeError,ImportError,AssertionError) as exc:
  print(json.dumps({'passed':False,'error_type':type(exc).__name__,'error':str(exc)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
