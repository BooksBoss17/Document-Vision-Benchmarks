"""Strict, standalone v1.2 data contract; Python3.11+, Pillow for --decode."""
from pathlib import Path
from collections import Counter
import json,hashlib,re,math
CLASSES=['decoration','heading_or_footer','transcribable_content','diagram']
PROTOCOLS=['semantic_context_v12','image_only_diagnostic_v12']
MAX_JSON_BYTES=64*1024*1024
def need(value,message):
 if not value:raise ValueError(message)
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
def pairs(values):
 result={}
 for k,v in values:need(k not in result,'duplicate JSON key:'+str(k));result[k]=v
 return result
def loads(text):
 def bad(value):raise ValueError('non-finite JSON value:'+value)
 return json.loads(text,object_pairs_hook=pairs,parse_constant=bad)
def read(path):
 path=Path(path);need(path.stat().st_size<=MAX_JSON_BYTES,'JSON_CAPACITY_EXCEEDED');return loads(path.read_text(encoding='utf-8'))
def rows(path):
 path=Path(path);need(path.stat().st_size<=MAX_JSON_BYTES,'JSONL_CAPACITY_EXCEEDED')
 with path.open(encoding='utf-8') as f:
  return [loads(l) for l in f if l.strip()]
def write(path,value):
 with Path(path).open('x',encoding='utf-8',newline='\n') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
def jsonl(path,values):
 with Path(path).open('x',encoding='utf-8',newline='\n') as f:
  for v in values:f.write(json.dumps(v,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
def canonical(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()
def member(root,relative):
 need(isinstance(relative,str) and relative and '\\' not in relative,'invalid member path')
 p=Path(relative);root=Path(root).resolve();result=(root/p).resolve()
 need(not p.is_absolute() and '..' not in p.parts and result.is_relative_to(root),'member escapes dataset')
 return result
def outside(root,path):
 path=Path(path).resolve();need(not path.is_relative_to(Path(root).resolve()),'output must be outside frozen package');return path
def integrity(root):
 from authority import CORE_PINS,EXPECTED_VIEWS,EXPECTED_BOOKS
 root=Path(root).resolve();manifest=root/'checksums.sha256';entries={}
 for line in manifest.read_text(encoding='utf-8').splitlines():
  digest,rel=line.split('  ',1);need(re.fullmatch('[0-9a-f]{64}',digest) is not None and rel not in entries,'invalid checksum member');p=member(root,rel);need(p.is_file() and sha(p)==digest,'payload hash mismatch:'+rel);entries[rel]=digest
 actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p!=manifest}
 need(actual==set(entries),'manifest coverage mismatch')
 for rel,digest in CORE_PINS.items():need(entries.get(rel)==digest and sha(member(root,rel))==digest,'fixed v1.2 core changed:'+rel)
 d=read(root/'dataset.json');need(d['version']=='1.2.0' and d['sample_count']==EXPECTED_VIEWS==8767 and d['book_count']==EXPECTED_BOOKS==12,'fixed dataset identity/count changed')
 return {'passed':True,'manifest_sha256':sha(manifest),'members':len(entries),'all_payload_members_hashed':True,'trust':'Candidate tool trusted by frozen source; installed official entry additionally requires independent registry pin.','model_execution_attested':False}
def inputs(root,protocol):
 need(protocol in PROTOCOLS,'unknown input protocol');name='model-inputs.jsonl' if protocol==PROTOCOLS[0] else 'image-only-inputs.jsonl';data=rows(Path(root)/'inputs'/name);seen=set()
 for r in data:
  expected={'sample_id','image','sha256','instruction','occurrences','primary_occurrence_index'} if protocol==PROTOCOLS[0] else {'sample_id','image','sha256','instruction'}
  need(isinstance(r,dict) and set(r)==expected,'model input whitelist mismatch');uid=r['sample_id'];need(isinstance(uid,str) and uid not in seen,'duplicate/invalid input UID');seen.add(uid)
  need(isinstance(r['sha256'],str) and re.fullmatch('[0-9a-f]{64}',r['sha256']) is not None,'invalid image digest');need(re.fullmatch('images/'+r['sha256']+r'\.(png|jpg|jpeg)',r['image']) is not None,'model image path must be answer-free hash path');need(member(root,r['image']).is_file(),'model image missing')
  need(isinstance(r['instruction'],str) and r['instruction'],'input instruction missing')
  if protocol==PROTOCOLS[0]:
   occ=r['occurrences'];idx=r['primary_occurrence_index'];need(isinstance(occ,list) and occ and type(idx) is int and 0<=idx<len(occ),'invalid primary occurrence')
   for o in occ:
    need(isinstance(o,dict) and set(o)=={'page_number','bbox_pdf_points','text_context'},'occurrence model whitelist mismatch');box=o['bbox_pdf_points'];need(type(o['page_number']) is int and o['page_number']>0 and isinstance(o['text_context'],str),'invalid model context')
    need(isinstance(box,list) and len(box)==4 and all(type(v) in (int,float) and math.isfinite(v) for v in box) and box[0]<box[2] and box[1]<box[3],'invalid source box')
 need(len(data)==8767,'input inventory count differs');return data
def selection(root,selection_path,public_inputs):
 full=[r['sample_id'] for r in public_inputs];path=Path(selection_path).resolve() if selection_path else (Path(root)/'selections/all.json').resolve();s=read(path)
 need(set(s)=={'schema_version','inputs_sha256','sample_ids'} and s['schema_version']==1,'selection schema mismatch');need(s['inputs_sha256']==sha(Path(root)/'inputs/model-inputs.jsonl'),'selection input identity mismatch')
 uids=s['sample_ids'];need(isinstance(uids,list) and uids and all(isinstance(u,str) for u in uids) and len(uids)==len(set(uids)) and set(uids)<=set(full),'invalid selection members')
 if path==(Path(root)/'selections/all.json').resolve():need(uids==full,'official full selection truncated');scope='OFFICIAL_FULL_8767'
 elif path.is_relative_to((Path(root)/'selections').resolve()):scope='REGISTERED_DIAGNOSTIC_SUBSET_NOT_FULL_SCORE'
 else:scope='CUSTOM_SUBSET_NOT_OFFICIAL_SCORE'
 return {'sample_ids':uids,'selection_sha256':sha(path),'scope':scope,'selection_path':str(path)}
def predictions(path,allowed):
 path=Path(path);need(path.stat().st_size<=MAX_JSON_BYTES,'PREDICTION_FILE_CAPACITY_EXCEEDED');out={}
 with path.open(encoding='utf-8') as stream:
  while True:
   line=stream.readline(4097)
   if not line:break
   need(len(line)<=4096,'PREDICTION_LINE_CAPACITY_EXCEEDED')
   if not line.strip():continue
   row=loads(line);need(isinstance(row,dict) and set(row)=={'sample_id','label'},'prediction must contain exactly sample_id,label');uid=row['sample_id'];label=row['label'];need(isinstance(uid,str) and uid in allowed and uid not in out,'duplicate/unknown prediction UID:'+str(uid));need(isinstance(label,str) and label in CLASSES+['UNRESOLVED'],'unknown prediction label');out[uid]=label;need(len(out)<=len(allowed),'prediction record capacity exceeded')
 return out
def validate(root,decode=False):
 root=Path(root).resolve();proof=integrity(root);I=inputs(root,PROTOCOLS[0]);J=inputs(root,PROTOCOLS[1]);G=rows(root/'annotations/gold.jsonl');R=rows(root/'annotations/records.jsonl');uid_list=[r['sample_id'] for r in I]
 need(uid_list==[r['sample_id'] for r in J]==[r['sample_id'] for r in G]==[r['sample_id'] for r in R],'UID ordering or membership mismatch')
 d=read(root/'dataset.json');books=read(root/'sources/books.json');adjud=read(root/'review/adjudications.json');changes={r['sample_id']:r for r in adjud['changes']};old={};old_context={};old_counts={}
 for b in books:
  prefix='provenance/v11/'+b['legacy_folder']+'/'
  old_rows=rows(root/(prefix+'manifest.jsonl'));old.update({r['sample_id']:r for r in old_rows});old_counts[b['code']]=len(old_rows);old_context.update({r['sample_id']:r for r in rows(root/(prefix+'source-context.jsonl'))})
 need(set(old)==set(uid_list) and len(changes)==13,'ancestor inventory/curation scope mismatch');counts=Counter();book_counts=Counter();boundary=0
 for i,j,g,r in zip(I,J,G,R):
  uid=i['sample_id'];original=old[uid];change=changes.get(uid);need(g['label'] in CLASSES and g['status']=='ADMITTED' and g['human_verified'] is False and type(g['boundary_case']) is bool and type(g['requires_keep']) is bool and type(g['v12_curated']) is bool,'invalid gold state')
  expected_label=change['new_label'] if change else original['label'];need(g['label']==expected_label and r['legacy_label']==original['label'],'unapproved label change');need(i['sha256']==j['sha256']==r['sha256']==original['sha256'],'original pixels/UID binding changed');need(i['image']==j['image']==r['image'],'public/record image binding mismatch')
  if change:need(change['old_label']==original['label'] and change['image_sha256']==original['sha256'],'curation does not bind ancestor')
  need(g['v12_curated']==bool(change) and g['boundary_case']==original['boundary_case'],'curation/boundary membership changed');need(g['label_origin']==('V12_TARGETED_AI_ADJUDICATION' if change else original['label_origin']),'evidence layer changed')
  for field in ['source_pdf_sha256','source_pdf_page_number','source_bbox_pdf_pt','width','height','mode']:need(r[field]==original[field],'original geometry/source metadata changed:'+field)
  if change:
   for evidence in change['evidence']:need(sha(member(root,evidence['file']))==evidence['sha256'],'curation evidence bytes changed')
  source=old_context[uid];occ=source['occurrences'];safe=[{k:o[k] for k in ['page_number','bbox_pdf_points','text_context']} for o in occ];need(i['occurrences']==safe,'source occurrence content changed')
  primary=i['occurrences'][i['primary_occurrence_index']];need(primary['page_number']==original['source_pdf_page_number'] and primary['bbox_pdf_points']==original['source_bbox_pdf_pt'],'primary source mismatch');need(r['is_table']==('/表格/' in original['image']),'packaged table flag mismatch')
  need(g['book_code']==r['book_code'] and g['label_origin']==r['label_origin'] and g['requires_keep']==(g['label']!='decoration') and g['explicit_historical_user_adjudication']==r['explicit_historical_user_adjudication'],'gold/source metadata conflict');counts[g['label']]+=1;book_counts[g['book_code']]+=1;boundary+=g['boundary_case']
 need(dict(counts)==d['class_counts'] and dict(book_counts)==old_counts==d['book_counts'] and boundary==d['boundary_count']==6,'declared counts differ')
 need(sum(r['is_table'] for r in R)==d['table_sample_count'],'table count differs');need(sum(g['explicit_historical_user_adjudication'] for g in G)==45,'historical user adjudication membership differs');need(len({r['sha256'] for r in R})==d['unique_image_count']==8564,'unique image count differs')
 decoded=0
 if decode:
  from PIL import Image
  checked=set()
  for r in R:
   if r['image'] in checked:continue
   with Image.open(member(root,r['image'])) as im:im.load();need(im.size==(r['width'],r['height']) and im.mode==r['mode'],'decoded image metadata mismatch')
   checked.add(r['image']);decoded+=1
 return {'passed':True,'version':'1.2.0','samples':len(G),'books':len(books),'curated_views':len(changes),'decoded_unique_images':decoded,'class_counts':dict(counts),'boundary_views':boundary,'historical_user_adjudication_views':45,'package_integrity':proof,'human_verified':False,'fresh_original_pdf_verification_claimed':False}
