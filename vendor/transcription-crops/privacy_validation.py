"""Validate explicit privacy derivatives without fabricating original inference authentication."""
import hashlib,json,zipfile,io
from pathlib import Path
from PIL import Image,ImageDraw
def need(ok,msg):
 if not ok:raise ValueError(msg)
def read(p):return json.loads(Path(p).read_text(encoding='utf8'))
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def rows(p):return [json.loads(s) for s in Path(p).read_text('utf8').splitlines() if s.strip()]
def verify_asset_record(root,item):
 payloads=[]
 for rel in item['paths']:
  p=(root/rel).resolve();need(p.is_relative_to(root.resolve()) and p.is_file(),'privacy asset path missing/escaping');payloads.append(p.read_bytes())
 for occurrence in item.get('embedded_occurrences',[]):
  p=(root/occurrence['path']).resolve();need(p.is_relative_to(root.resolve()) and p.is_file(),'privacy source path missing/escaping');need(occurrence['kind']=='office-media','unsupported embedded privacy proof')
  with zipfile.ZipFile(p) as z:payloads.append(z.read(occurrence['part']))
 need(payloads,'privacy asset has no verified occurrences')
 for blob in payloads:
  need(hashlib.sha256(blob).hexdigest()==item['current_sha256'],'privacy asset bytes differ')
  with Image.open(io.BytesIO(blob)) as im:
   rgba=im.convert('RGBA');need(list(im.size)==[item['width'],item['height']],'privacy asset size differs');mask=Image.new('L',im.size,255);d=ImageDraw.Draw(mask)
   for x0,y0,x1,y1 in item['rectangles_px']:
    need(0<=x0<x1<=im.width and 0<=y0<y1<=im.height,'privacy asset rectangle invalid');d.rectangle((x0,y0,x1-1,y1-1),fill=0);need(all(c==(255,255,255,255) for c in rgba.crop((x0,y0,x1,y1)).getdata()),'privacy asset mask is not white')
   h=hashlib.sha256();pixels=rgba.tobytes()
   for i,v in enumerate(mask.tobytes()):
    if v:h.update(pixels[i*4:i*4+4])
   need(h.hexdigest()==item['outside_rgba_sha256'],'privacy asset unmasked pixels differ')
 return True
def verify_lineage(root,sample):
 proof=read(root/'privacy/lineage.json');matches=[x for x in proof['images'] if x.get('sample_id')==sample['sample_id']]
 need(len(matches)==1,'missing or ambiguous privacy sample lineage');item=matches[0]
 need(sample['sample_id']=='sample-'+item['original_sha256'][:20] and sample['image_sha256']==item['current_sha256'],'privacy stable identity differs')
 p=root/'baseline'/sample['image'];need(sha(p)==item['current_sha256'],'privacy image content differs')
 with Image.open(p) as im:
  rgba=im.convert('RGBA');need(list(im.size)==[item['width'],item['height']],'privacy image size differs')
  mask=Image.new('1',im.size,1);d=ImageDraw.Draw(mask)
  for x0,y0,x1,y1 in item['rectangles_px']:
   need(0<=x0<x1<=im.width and 0<=y0<y1<=im.height,'privacy rectangle outside image');d.rectangle((x0,y0,x1-1,y1-1),fill=0)
   need(all(c==(255,255,255,255) for c in rgba.crop((x0,y0,x1,y1)).getdata()),'privacy rectangle is not white')
  # Streaming rows keeps memory bounded and avoids a NumPy dependency for consumers.
  h=hashlib.sha256();pixels=rgba.tobytes();keep=mask.tobytes();mask_pixels=mask.convert('L').tobytes()
  for i,v in enumerate(mask_pixels):
   if v:h.update(pixels[i*4:i*4+4])
  need(h.hexdigest()==item['outside_rgba_sha256'],'pixels outside privacy rectangles differ')
 return True
def validate_derivative_baseline(root):
 root=Path(root).resolve();base=root/'baseline';marker=read(base/'PRIVACY_DERIVATIVE.json');proof=read(root/'privacy/lineage.json')
 need(marker['schema']=='transcription-privacy-derived-baseline-v1' and marker['original_model_authentication_applies_to_edited_inputs'] is False,'privacy authority scope differs')
 descriptor=read(base/'dataset.json');need(descriptor.get('privacy_derivative') is True and descriptor['version']==marker['derivative_version'],'baseline derivative version differs')
 need(sha(root/'privacy/lineage.json')==marker['lineage_sha256'],'privacy lineage fingerprint differs')
 for item in proof['images']+proof.get('additional_images',[]):verify_asset_record(root,item)
 listed={}
 for line in (base/'checksums.sha256').read_text('utf8').splitlines():
  h,rel=line.split('  ',1);p=(base/rel).resolve();need(p.is_relative_to(base.resolve()) and p.is_file() and rel not in listed,'baseline manifest path invalid');need(sha(p)==h,'derived baseline checksum differs: '+rel);listed[rel]=h
 actual={p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file() and p!=base/'checksums.sha256'};need(actual==set(listed),'derived baseline file set differs')
 for p in base.rglob('*.json'):read(p)
 for p in base.rglob('*.jsonl'):rows(p)
 source_rows=rows(base/'sources/manifest.jsonl');need(len(source_rows)==131 and len({x['source_id'] for x in source_rows})==131,'privacy source population differs')
 for x in source_rows:
  p=base/x['relative_path'];need(sha(p)==x['sha256'] and p.stat().st_size==x['bytes'],'privacy current source manifest differs')
 samples=rows(base/'samples/samples.jsonl');need(len(samples)==944 and len({x['sample_id'] for x in samples})==944,'privacy sample population differs')
 source_index={x['source_id']:x for x in source_rows}
 for x in samples:
  if x['sample_id']!='sample-'+x['image_sha256'][:20]:verify_lineage(root,x)
  for binding in x['source_bindings']:
   if binding.get('source_id') is None:continue
   need(binding['source_id'] in source_index,'privacy binding source missing');source=source_index[binding['source_id']]
   need(binding['source_sha256']==source['sha256'] and binding['source_file']==source['relative_path'],'privacy sample/current-source binding differs')
 need(proof['original_validation']['status']=='PASS' and proof['original_validation']['version']=='1.1.0','missing before-edit full validation')
 return dict(status='PASS',version=marker['derivative_version'],scope='Privacy-derived baseline bytes, JSON structures, source population and provenance; original historical inference chain is not re-certified against changed inputs',files=len(listed),sources=131,samples=944,original_model_authentication_applies_to_edited_inputs=False,human_verified=False)
