"""Independent v2 package validation. No model calls, no mutation of the package."""
from pathlib import Path
from collections import Counter, defaultdict
import argparse
import hashlib
import json
import math
try:
    from .final_visual_admission import FINAL_UID, validate_visual
    from .review_binding import validate_review_proof
    from .source_admission import validate_source_proof
except ImportError:
    from final_visual_admission import FINAL_UID, validate_visual
    from review_binding import validate_review_proof
    from source_admission import validate_source_proof

PROTOCOLS = {'REGION_TRANSCRIPTION', 'PAGE_TRANSCRIPTION', 'PAIRED_INTERFERENCE'}
FORMATS = {'TEXT', 'LATEX', 'TABLE_HTML', 'BLOCKS'}

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key ' + repr(key))
        result[key] = value
    return result

def load_json(text):
    def invalid_constant(value):
        raise ValueError('nonfinite JSON number ' + value)
    return json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid_constant)

def answer_fields(value, prefix=''):
    """Reject answer/review fields anywhere in the public input object."""
    forbidden = {'reference_text', 'gold', 'answer', 'expected_answer', 'expected_text',
                 'review_status', 'eligible', 'review_evidence', 'source_evidence', 'source_admission', 'review_binding', 'reference_sha256'}
    if isinstance(value, dict):
        for key, child in value.items():
            path = prefix + '.' + key if prefix else key
            if key in forbidden:
                yield path
            yield from answer_fields(child, path)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from answer_fields(child, prefix + '[' + str(i) + ']')

def valid_box(box):
    return (isinstance(box, list) and len(box) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in box)
            and box[0] < box[2] and box[1] < box[3])

def pair_geometry(root, members, records, decode, controlled_sources=None):
    """Check same source coordinates and exact target pixels, one pair at a time.

    The assembler uses translation-only source_crop_box (scale1/padding0).
    Source coordinates may be fractional; windows use floor starts/ceil ends.
    """
    from PIL import Image, ImageChops
    if len(members) != 2:
        raise ValueError('pair geometry requires two members')
    if {r.get('pair_variant') for r in members} == {'figure_removed','figure_present'}:
        return controlled_geometry(root,members,records,decode,controlled_sources)
    regions, windows, source_ids = [], [], []
    for row in members:
        rec = records[row['sample_uid']]
        binding, transform = rec['source_binding'], rec['geometry_transform']
        box = transform.get('source_crop_box')
        if not valid_box(box) or transform.get('scale') != 1 or transform.get('padding') != 0:
            raise ValueError('unsupported/invalid source geometry transform')
        if any(type(v) is not int for v in box):
            raise ValueError('source crop must use assembler integer pixel bounds')
        if (box[2] - box[0], box[3] - box[1]) != (row['width'], row['height']):
            raise ValueError('source crop dimensions differ from image')
        if not (0 <= box[0] < box[2] <= binding['original_width']
                and 0 <= box[1] < box[3] <= binding['original_height']):
            raise ValueError('source crop outside original image')
        source_ids.append((binding.get('dataset'), binding.get('source_id'), binding.get('image_sha256')))
        absolute, local = {}, {}
        for region in row['task']['target_regions']:
            rid, b = region['region_id'], region['bbox']
            if not valid_box(b):
                raise ValueError('invalid pair ROI')
            if not (0 <= b[0] < b[2] <= row['width'] and 0 <= b[1] < b[3] <= row['height']):
                raise ValueError('pair ROI outside image')
            absolute[rid] = [b[0] + box[0], b[1] + box[1], b[2] + box[0], b[3] + box[1]]
            local[rid] = [math.floor(b[0]), math.floor(b[1]), math.ceil(b[2]), math.ceil(b[3])]
        regions.append(absolute)
        windows.append(local)
    if source_ids[0] != source_ids[1]:
        raise ValueError('pair source identity differs')
    if not regions[0] or set(regions[0]) != set(regions[1]):
        raise ValueError('pair target region IDs differ')
    for rid in regions[0]:
        if not all(math.isclose(a, b, rel_tol=0, abs_tol=1e-6)
                   for a, b in zip(regions[0][rid], regions[1][rid])):
            raise ValueError('pair absolute source ROI differs ' + rid)
    # Verify source floor/ceil windows independently of fractional subtraction.
    for i, row in enumerate(members):
        origin = records[row['sample_uid']]['geometry_transform']['source_crop_box']
        for rid, b in regions[i].items():
            source_window = [math.floor(b[0]), math.floor(b[1]), math.ceil(b[2]), math.ceil(b[3])]
            local_window = [windows[i][rid][0] + origin[0], windows[i][rid][1] + origin[1],
                            windows[i][rid][2] + origin[0], windows[i][rid][3] + origin[1]]
            if source_window != local_window:
                raise ValueError('pair source pixel window differs ' + rid)
    if decode:
        with Image.open(safe_path(root, members[0]['image'])) as left, Image.open(safe_path(root, members[1]['image'])) as right:
            if any(im.size != (row['width'], row['height']) for im, row in zip((left,right),members)):
                raise ValueError('pair decoded dimensions differ from declared dimensions')
            for rid in regions[0]:
                a = left.crop(windows[0][rid]).convert('RGB')
                b = right.crop(windows[1][rid]).convert('RGB')
                try:
                    if a.size != b.size or ImageChops.difference(a, b).getbbox() is not None:
                        raise ValueError('pair target pixels differ ' + rid)
                finally:
                    a.close()
                    b.close()


def controlled_geometry(root, members, records, decode, source_cache=None):
    """Rebuild both conditions independently from immutable source shard + bitmap.

    No use of the assembler/masking implementation. Work in 64-row strips and
    keep at most one source pair decoded; no image cache grows with population.
    """
    from PIL import Image, ImageChops, ImageDraw
    by={r['pair_variant']:r for r in members}
    removed,present=by['figure_removed'],by['figure_present']
    if removed['task'] != present['task']:
        raise ValueError('controlled pair literal tasks differ')
    if (removed['width'],removed['height']) != (present['width'],present['height']):
        raise ValueError('controlled pair canvas dimensions differ')
    if any(r.get('metadata',{}).get('pair_design')!='SAME_CANVAS_FIGURE_ABLATION' for r in members):
        raise ValueError('controlled pair design missing')
    recs=[records[r['sample_uid']] for r in members]
    if recs[0]['source_binding'] != recs[1]['source_binding']:
        raise ValueError('controlled pair source identity differs')
    proof=recs[0].get('controlled_pair_source')
    if not isinstance(proof,dict) or any(rec.get('controlled_pair_source')!=proof for rec in recs):
        raise ValueError('controlled pair source shard proof differs/missing')
    shard=Path(proof['shard_path']).resolve()
    source_cache={} if source_cache is None else source_cache
    key=str(shard)
    if key not in source_cache:
        if sha(shard)!=proof['shard_sha256']:raise ValueError('controlled pair source shard changed')
        source_cache[key]={'sha256':proof['shard_sha256'],'rows':{r['sample_uid']:r for r in read_rows(shard)}}
    if source_cache[key]['sha256']!=proof['shard_sha256']:raise ValueError('controlled shard fingerprint conflict')
    source_row=source_cache[key]['rows'].get(proof['legacy_uid'])
    if source_row is None or any(r['legacy_uid']!=proof['legacy_uid'] for r in members):
        raise ValueError('controlled pair source lineage differs')
    binding=recs[0]['source_binding']
    if binding!=source_row['source_binding']:
        raise ValueError('controlled pair binding differs from frozen source shard')
    source_path=Path(binding['image_path'])
    if sha(source_path)!=binding['image_sha256']:
        raise ValueError('controlled pair source image changed')
    with Image.open(source_path) as source:
        w,h=source.size
        if w*h>100_000_000:raise ValueError('controlled pair source exceeds bounded pixel budget')
        if [w,h]!=[binding['original_width'],binding['original_height']]:
            raise ValueError('controlled source original dimensions differ')
        if any((r['width'],r['height'])!=(w,h) for r in members):
            raise ValueError('controlled canvas differs from source dimensions')
        def window(box):
            if not valid_box(box):raise ValueError('controlled source box invalid')
            b=[max(0,math.floor(box[0])),max(0,math.floor(box[1])),min(w,math.ceil(box[2])),min(h,math.ceil(box[3]))]
            if not valid_box(b):raise ValueError('controlled source box outside image')
            return b
        targets=[]
        for i,region in enumerate(source_row.get('target_regions',[])):
            b=region['bbox'];b=[max(0,min(w,b[0])),max(0,min(h,b[1])),max(0,min(w,b[2])),max(0,min(h,b[3]))]
            if not valid_box(b):raise ValueError('controlled source target invalid')
            rid='r-'+hashlib.sha256((proof['legacy_uid']+':'+str(region.get('source_annotation_id',i))).encode()).hexdigest()[:20]
            targets.append({'region_id':rid,'bbox':b})
        targets.sort(key=lambda r:r['region_id'])
        if not targets or any(r['task']['target_regions']!=targets for r in members):
            raise ValueError('controlled absolute target ROI differs from source')
        figures=source_row.get('source_figure_regions',[])
        if not figures:raise ValueError('controlled pair fabricated without figure boxes')
        fb=sorted({tuple(window(f['bbox'])) for f in figures})
        tb=sorted({tuple(window(t['bbox'])) for t in targets})
        expected_recipe={'version':1,'algorithm':'WHITE_FIGURES_EXCEPT_TARGETS_RGB_V1','fill':[255,255,255],
                         'source_size':[w,h],'figure_boxes':[list(b) for b in fb],'protected_boxes':[list(b) for b in tb]}
        for row,rec in zip(members,recs):
            transform=rec['geometry_transform']
            if transform.get('source_crop_box')!=[0,0,w,h] or transform.get('scale')!=1 or transform.get('padding')!=0:
                raise ValueError('controlled pair must preserve full source canvas')
            if transform.get('view_kind')!=row['pair_variant'] or transform.get('mask_recipe')!=expected_recipe:
                raise ValueError('controlled mask recipe differs from independent source derivation')
            if rec.get('source_figure_regions')!=figures:
                raise ValueError('controlled figure provenance differs from source')
            cache_value=dict(source_sha256=binding['image_sha256'],source_crop_box=[0,0,w,h],view_kind=row['pair_variant'],mask_recipe=expected_recipe)
            expected_key=hashlib.sha256(json.dumps(cache_value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if transform.get('image_cache_key')!=expected_key:
                raise ValueError('controlled image cache fingerprint differs')
        if not decode:return
        changed,extent=0,None
        with Image.open(safe_path(root,removed['image'])) as rm,Image.open(safe_path(root,present['image'])) as pr:
            if rm.size!=(w,h) or pr.size!=(w,h):raise ValueError('controlled decoded canvas dimensions differ')
            for top in range(0,h,64):
                bottom=min(h,top+64);strip=(0,top,w,bottom)
                with source.crop(strip).convert('RGB') as original,rm.crop(strip).convert('RGB') as actual,pr.crop(strip).convert('RGB') as unmodified:
                    with ImageChops.difference(original,unmodified) as diff:
                        if diff.getbbox():raise ValueError('figure_present differs from source pixels')
                    expected=original.copy()
                    try:
                        draw=ImageDraw.Draw(expected)
                        for x0,y0,x1,y1 in fb:
                            if y0<bottom and y1>top:draw.rectangle((x0,max(y0,top)-top,x1-1,min(y1,bottom)-top-1),fill='white')
                        for x0,y0,x1,y1 in tb:
                            if y0<bottom and y1>top:
                                local=(x0,max(y0,top)-top,x1,min(y1,bottom)-top)
                                with original.crop(local) as patch:expected.paste(patch,(local[0],local[1]))
                        with ImageChops.difference(expected,actual) as diff:
                            if diff.getbbox():raise ValueError('figure_removed pixels violate independently reconstructed mask/target protection')
                        with ImageChops.difference(original,actual) as diff:
                            channels=diff.split()
                            try:
                                with ImageChops.lighter(channels[0],channels[1]) as rg:
                                    with ImageChops.lighter(rg,channels[2]) as mask:
                                        changed+=sum(mask.histogram()[1:]);bb=mask.getbbox()
                            finally:
                                for c in channels:c.close()
                            if bb:
                                bb=[bb[0],bb[1]+top,bb[2],bb[3]+top]
                                extent=bb if extent is None else [min(extent[0],bb[0]),min(extent[1],bb[1]),max(extent[2],bb[2]),max(extent[3],bb[3])]
                    finally:expected.close()
        overlap=any(min(f[2],t[2])>max(f[0],t[0]) and min(f[3],t[3])>max(f[1],t[1]) for f in fb for t in tb)
        reasons=[]
        if overlap:reasons.append('FIGURE_TARGET_OVERLAP')
        if not changed:reasons.append('NO_EFFECTIVE_FIGURE_INTERVENTION')
        expected_quality=dict(qualified=not reasons,reasons=reasons,changed_pixel_count=changed,changed_bbox=extent,figure_target_overlap=overlap)
        for rec in recs:
            if rec['geometry_transform'].get('pair_qualification')!=expected_quality:
                raise ValueError('controlled pair qualification differs from source pixel evidence')
            if reasons and (rec.get('eligible') is not False or rec.get('review_status')!='QUARANTINED' or not set(reasons)<=set(rec.get('quarantine_reasons',[]))):
                raise ValueError('unqualified controlled pair must remain quarantined')

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

def read_rows(path):
    result = []
    seen = set()
    with Path(path).open(encoding='utf-8-sig') as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            row = load_json(line)
            if not isinstance(row, dict):
                raise ValueError(f'{path}:{line_no}: row must be an object')
            uid = row.get('sample_uid')
            if not isinstance(uid, str) or not uid or uid in seen:
                raise ValueError(f'{path}:{line_no}: invalid/duplicate sample_uid {uid!r}')
            seen.add(uid)
            result.append(row)
    return result

def safe_path(root, rel):
    if not isinstance(rel, str) or not rel or '\\' in rel:
        raise ValueError('image must use a nonempty POSIX relative path')
    candidate = Path(rel)
    if candidate.is_absolute() or '..' in candidate.parts:
        raise ValueError('path must stay inside package')
    result = (root / candidate).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError('resolved path escapes package')
    return result

def validate(root, decode=True):
    root = Path(root).resolve()
    descriptor = load_json((root / 'dataset.json').read_text('utf-8-sig'))
    inputs = read_rows(root / 'inputs/model-inputs.jsonl')
    gold = read_rows(root / 'annotations/gold.jsonl')
    records = read_rows(root / 'annotations/records.jsonl')
    ib, gb, rb = ({r['sample_uid']: r for r in rows} for rows in (inputs, gold, records))
    errors = []
    def need(ok, message):
        if not ok:
            errors.append(message)
    need(set(ib) == set(gb) == set(rb), 'inputs/gold/records UID mismatch')
    need(descriptor.get('version') == '2.3.0', 'version must be2.3.0')
    need(descriptor.get('human_verified') is False, 'AI-assisted status must remain explicit')
    need(descriptor.get('total_samples') == len(inputs), 'descriptor sample count mismatch')
    expected_visual={r['sample_uid'] for r in records if r.get('eligible') and not r.get('resolution_admitted') and r.get('review_status') in {'REVIEWED','CORRECTED_REVIEWED'}}
    expected_local={r['sample_uid'] for r in records if r.get('eligible') and r.get('resolution_admitted') is True and r.get('review_status')=='CORRECTED_REVIEWED'}
    need(descriptor.get('visual_reviewed_samples')==len(expected_visual),'descriptor must separate inherited full-target visual from local correction')
    need(descriptor.get('resolution_corrected_samples')==len(expected_local),'descriptor local correction count')
    for name,expected in [('visual-reviewed-all',expected_visual),('resolution-corrected-all',expected_local)]:
        try:
            selection=load_json((root/'selections'/(name+'.json')).read_text('utf-8'))
            need(set(selection.get('sample_uids',[]))==expected,name+': provenance selection differs')
            need(selection.get('inputs_sha256')==sha(root/'inputs/model-inputs.jsonl'),name+': selection input hash')
        except (ValueError,OSError,TypeError) as exc:
            errors.append(name+': invalid provenance selection '+str(exc))
    hashes = {}
    source_cache = {'files': {}}
    pairs = defaultdict(list)
    controlled_sources={}
    source_groups = defaultdict(set)
    for uid, row in ib.items():
        g, rec = gb.get(uid, {}), rb.get(uid, {})
        need(row.get('protocol') in PROTOCOLS, uid + ': protocol')
        need(isinstance(row.get('group_id'), str) and bool(row['group_id']), uid + ': group')
        for forbidden in answer_fields(row):
            errors.append(uid + ': answer/review field exposed ' + forbidden)
        need(g.get('reference_format') in FORMATS, uid + ': format')
        need(isinstance(g.get('reference_text'),str),uid+': reference text schema')
        need(type(g.get('eligible')) is bool, uid + ': eligible must be bool')
        need(type(row.get('width')) is int and row['width']>0 and type(row.get('height')) is int and row['height']>0,uid+': dimensions schema')
        need(g.get('review_status') in {'SOURCE_VERIFIED','REVIEWED','CORRECTED_REVIEWED','PENDING','QUARANTINED','UNSUPPORTED_TASK'}, uid + ': review status')
        need(g.get('review_status')!='UNSUPPORTED_TASK' or g.get('eligible') is False,uid+': UNSUPPORTED_TASK cannot be eligible')
        if uid == FINAL_UID:
            try:
                validate_visual(root,row,g,rec,cache=source_cache)
            except (OSError,ValueError,KeyError,TypeError) as e:
                errors.append(uid+': final visual evidence invalid '+str(e))
        if g.get('eligible'):
            need('UNSUPPORTED_TASK' not in g.get('quarantine_reasons',[]), uid + ': unsupported task cannot be eligible')
            if g.get('review_status') == 'SOURCE_VERIFIED' or rec.get('resolution_admitted') is True:
                try:
                    validate_source_proof(root,row,g,rec,cache=source_cache)
                except (OSError,ValueError,KeyError,TypeError) as e:
                    errors.append(uid+': source evidence invalid '+str(e))
            else:
                need(g.get('review_status') in {'REVIEWED', 'CORRECTED_REVIEWED'}, uid + ': unreviewed scoring admission')
                need(bool(rec.get('review_evidence')), uid + ': eligible lacks review evidence')
                for evidence in rec.get('review_evidence',[]):
                    try:
                        ep=safe_path(root,evidence['path'])
                        need(sha(ep)==evidence.get('sha256'),uid+': receipt hash')
                        proof=load_json(ep.read_text('utf-8'))
                        need(proof.get('sample_uid')==uid and proof.get('eligible') is True,uid+': receipt admission')
                        need(proof.get('verdict')=='PASS',uid+': receipt verdict must PASS')
                        need(proof.get('image_sha256')==row.get('image_sha256'),uid+': receipt image binding')
                        need(proof.get('reference_sha256')==hashlib.sha256(g.get('reference_text','').encode('utf-8')).hexdigest(),uid+': receipt gold binding')
                        raw_name=proof['raw_receipt_filename']
                        if not isinstance(raw_name,str) or not raw_name or '/' in raw_name or '\\' in raw_name or Path(raw_name).is_absolute() or raw_name in {'.','..'}:
                            raise ValueError('raw receipt must be a sibling filename')
                        raw_path=safe_path(root,(ep.parent/raw_name).relative_to(root).as_posix())
                        need(sha(raw_path)==proof.get('raw_receipt_sha256'),uid+': raw receipt binding')
                        validate_review_proof(proof,row,g)
                        if {'transfer_evidence_path','transfer_evidence_sha256'} & proof.keys():
                            transfer_path=safe_path(root,proof['transfer_evidence_path'])
                            need(sha(transfer_path)==proof.get('transfer_evidence_sha256'),uid+': transfer evidence hash')
                            transfer=load_json(transfer_path.read_text('utf-8'))
                            need(transfer.get('sample_uid')==uid and transfer.get('raw_receipt_sha256')==proof.get('raw_receipt_sha256'),uid+': transfer raw/UID binding')
                            need(transfer.get('review_binding')==proof['review_binding'] and transfer.get('review_binding_sha256')==proof['review_binding_sha256'],uid+': transfer semantic binding')
                    except (OSError,ValueError,KeyError,TypeError) as e:
                        errors.append(uid+': review evidence invalid '+str(e))
        task = row.get('task', {})
        need(isinstance(task.get('instruction'), str) and bool(task['instruction'].strip()), uid + ': instruction')
        need(bool(task.get('output_format')), uid + ': output format')
        policy=task.get('table_serialization_policy')
        need(policy is None or (policy=='TABLE_LAYOUT_V1' and task.get('output_format')=='TABLE_HTML' and policy in task.get('instruction','')),uid+': table policy')
        need(task.get('annotation_policy') in (None,'CASIA_NATIVE_LABELLED_TEXT_V1'),uid+': annotation policy')
        profile=task.get('serialization_profile')
        need(profile is None or (profile=='UPSTREAM_TEX_MIXED_V1' and (task.get('output_format')=='TEXT' or
             (row.get('protocol')=='PAGE_TRANSCRIPTION' and task.get('output_format')=='PAGE_BLOCKS' and
              isinstance(task.get('region_formats'),dict) and 'TEXT' in task['region_formats'].values() and
              isinstance(g.get('blocks'),list) and any(b.get('reference_format')=='TEXT' for b in g['blocks'] if isinstance(b,dict))))),uid+': serialization profile')
        scope=task.get('scope')
        need(scope is None or (row.get('protocol')=='PAGE_TRANSCRIPTION' and scope in {'ANNOTATED_REGIONS','FULL_VISIBLE_CONTENT'}) or (row.get('protocol')!='PAGE_TRANSCRIPTION' and scope in {'SOURCE_ANNOTATED_REGION','UPSTREAM_TARGET_REFERENCE'}),uid+': task scope')
        need(not g.get('eligible') or task.get('support_status')!='UNSUPPORTED_TASK',uid+': unsupported task cannot be eligible')
        try:
            path = safe_path(root, row['image'])
            if row['image'] not in hashes:
                hashes[row['image']] = sha(path)
            need(hashes[row['image']] == row.get('image_sha256'), uid + ': image hash')
            if decode:
                from PIL import Image
                with Image.open(path) as im:
                    im.load()
                    need(im.size == (row.get('width'), row.get('height')), uid + ': dimensions')
            regions = task.get('target_regions', [])
            region_ids = []
            for region in regions:
                region_ids.append(region.get('region_id'))
                box = region.get('bbox', [])
                valid = valid_box(box)
                need(valid, uid + ': bbox schema')
                if valid:
                    need(0 <= box[0] < box[2] <= row['width'] and 0 <= box[1] < box[3] <= row['height'], uid + ': bbox bounds')
                need(set(region) <= {'region_id','bbox','polygon'}, uid + ': ROI extra fields leak labels/order')
            need(len(region_ids) == len(set(region_ids)) and all(isinstance(x,str) and x for x in region_ids), uid + ': ROI IDs')
            if row.get('protocol') == 'PAGE_TRANSCRIPTION':
                need(g.get('reference_format')=='BLOCKS',uid+': page gold must use BLOCKS')
                blocks=g.get('blocks')
                need(isinstance(blocks,list),uid+': page blocks schema')
                if isinstance(blocks,list):
                    block_ids=[]
                    for block in blocks:
                        if not isinstance(block,dict):
                            errors.append(uid+': block must be object')
                            continue
                        block_ids.append(block.get('region_id'))
                        need(isinstance(block.get('text'),str),uid+': block text schema')
                        need(block.get('reference_format') in FORMATS-{'BLOCKS'},uid+': block format')
                    need(all(isinstance(x,str) and x for x in block_ids) and len(block_ids)==len(set(block_ids)),uid+': gold block IDs')
                    need(set(block_ids)==set(region_ids),uid+': gold blocks/ROI mismatch')
                if 'unordered_region_ids' in task or 'unordered_region_ids' in g:
                    unordered=task.get('unordered_region_ids')
                    need(isinstance(unordered,list) and all(isinstance(x,str) for x in unordered)
                         and len(unordered)==len(set(unordered)) and set(unordered)<=set(region_ids),uid+': unordered IDs schema/scope')
                    need(g.get('unordered_region_ids')==unordered,uid+': gold/input unordered IDs mismatch')
                    need('region_formats' in task,uid+': unordered PAGE requires region_formats')
                if 'region_formats' in task:
                    formats=task['region_formats']
                    need(isinstance(formats,dict) and set(formats)==set(region_ids)
                         and all(v in FORMATS-{'BLOCKS'} for v in formats.values()),uid+': PAGE region_formats schema/scope')
                    if isinstance(blocks,list) and blocks:
                        need(formats=={b['region_id']:b['reference_format'] for b in blocks},uid+': PAGE region_formats differs from gold')
            else:
                need(not ({'region_formats','unordered_region_ids'} & task.keys()),uid+': PAGE-only scope fields')
            if row.get('protocol') == 'PAIRED_INTERFERENCE':
                need(bool(row.get('pair_id')), uid + ': pair id missing')
                need(bool(regions), uid + ': paired target scope missing')
                pairs[row.get('pair_id')].append(row)
        except (OSError, ValueError, KeyError, TypeError) as e:
            errors.append(uid + ': ' + str(e))
        for key in ('sample_uid','legacy_uid','protocol','category_path','image','image_sha256','width','height','group_id','task','pair_id','pair_variant','metadata'):
            need(rec.get(key) == row.get(key), uid + ': records/input mismatch ' + key)
        for key in ('reference_text','reference_format','review_status','eligible','blocks','source_binding','unordered_region_ids'):
            need(rec.get(key) == g.get(key), uid + ': records/gold mismatch ' + key)
        if row.get('group_id'):
            source_groups[row['group_id']].add(row.get('metadata',{}).get('partition','diagnostic'))
    for pid, members in pairs.items():
        need(len(members) == 2, str(pid) + ': pair requires exactly2 members')
        need({x.get('pair_variant') for x in members} in ({'crop','context'},{'figure_removed','figure_present'}), str(pid) + ': pair variants')
        need(len({x.get('group_id') for x in members}) == 1, str(pid) + ': pair group differs')
        refs = [gb.get(x['sample_uid'],{}) for x in members]
        need(len({(g.get('reference_format'),g.get('reference_text')) for g in refs}) == 1, str(pid) + ': pair answer differs')
        need(len({g.get('eligible') for g in refs}) == 1, str(pid) + ': pair admission differs')
        if {x.get('pair_variant') for x in members}=={'figure_removed','figure_present'}:
            for row in members:
                proof=rb.get(row['sample_uid'],{}).get('controlled_pair_source',{})
                need(descriptor.get('shard_fingerprints',{}).get(proof.get('shard_path'))==proof.get('shard_sha256') and bool(proof.get('shard_sha256')),
                     str(pid)+': controlled source proof not bound to dataset shard manifest')
        try:
            pair_geometry(root,members,rb,decode,controlled_sources)
        except (OSError,ValueError,KeyError,TypeError) as e:
            errors.append(str(pid)+': '+str(e))
    for group, partitions in source_groups.items():
        need(len(partitions) == 1, group + ': source group crosses partitions')
    for path,binding in controlled_sources.items():
        try:need(sha(path)==binding['sha256'],'controlled source shard drift during validation '+path)
        except OSError as e:errors.append('controlled source shard unavailable after validation '+str(e))
    if descriptor.get('controlled_pair_design')=='SAME_CANVAS_FIGURE_ABLATION':
        by_legacy=defaultdict(list)
        for row in inputs:
            if row.get('metadata',{}).get('inventory_role') != 'DERIVED_LIMITED_SCOPE':
                by_legacy[row.get('legacy_uid')].append(row)
        need(len(by_legacy)==descriptor.get('legacy_population'),'controlled legacy population mismatch')
        for legacy,members in by_legacy.items():
            core=[r for r in members if r.get('protocol')!='PAIRED_INTERFERENCE']
            need(len(core)==1,str(legacy)+': exactly one core PAGE/REGION required')
            need(len(members)-len(core) in {0,2},str(legacy)+': supplemental pair member count')
        need(sum(r.get('protocol')!='PAIRED_INTERFERENCE' and r.get('metadata',{}).get('inventory_role')!='DERIVED_LIMITED_SCOPE' for r in inputs)==descriptor.get('core_samples'),'controlled core sample count mismatch')
    derived=[r for r in inputs if r.get('metadata',{}).get('inventory_role')=='DERIVED_LIMITED_SCOPE']
    need(len(derived)==descriptor.get('derived_limited_scope_samples',0),'derived inventory mismatch')
    for r in derived:
        parent=r.get('metadata',{}).get('original_core_uid')
        need(parent in ib and ib[parent]['protocol']!='PAIRED_INTERFERENCE',r['sample_uid']+': derived parent missing')
        need(r['legacy_uid']==ib.get(parent,{}).get('legacy_uid'),r['sample_uid']+': derived legacy mismatch')
        need(r['task'].get('scope')=='ANNOTATED_REGIONS',r['sample_uid']+': derived task must be limited scope')
        need(rb[r['sample_uid']].get('resolution_admitted') is True,r['sample_uid']+': derived lacks resolution authority')
    # The leaf inventories are public entry points, so exact records must agree.
    expected_leaves=set()
    for category in sorted({r['category_path'] for r in records}):
        try:
            leaf=safe_path(root,category+'/samples.jsonl')
            expected_leaves.add(leaf)
            leaf_rows=read_rows(leaf)
            expected_rows={r['sample_uid']:r for r in records if r['category_path']==category}
            actual_rows={r['sample_uid']:r for r in leaf_rows}
            need(actual_rows==expected_rows,category+': leaf/records mismatch')
        except (OSError,ValueError,KeyError,TypeError) as e:
            errors.append(str(category)+': leaf invalid '+str(e))
    actual_leaves={p.resolve() for p in root.rglob('samples.jsonl') if '__pycache__' not in p.parts}
    need(actual_leaves==expected_leaves,'unexpected/missing leaf inventory')
    checksum_path = root / 'checksums.sha256'
    if checksum_path.exists():
        checksum_names = set()
        for line in checksum_path.read_text('utf-8').splitlines():
            digest, rel = line.split('  ',1)
            need(rel not in checksum_names, 'duplicate checksum path '+rel)
            checksum_names.add(rel)
            try:
                actual = hashes.get(rel) or sha(safe_path(root,rel))
                need(actual == digest, 'checksum '+rel)
            except (OSError, ValueError) as e:
                errors.append('checksum '+rel+': '+str(e))
        expected = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name != 'checksums.sha256'}
        need(expected == checksum_names, 'checksum coverage mismatch')
    else:
        errors.append('checksums.sha256 missing')
    for source_path,expected_hash in source_cache['files'].items():
        try:need(sha(source_path)==expected_hash,'source evidence drift during validation '+source_path)
        except OSError as e:errors.append('source evidence unavailable after validation '+str(e))
    return {'passed': not errors, 'samples':len(inputs), 'eligible':sum(g.get('eligible') is True for g in gold),
            'admission_counts':dict(Counter(g.get('review_status') for g in gold if g.get('eligible') is True)),
            'human_verified':False,'full_visible_content_review_claimed':False,
            'by_protocol':dict(Counter(x.get('protocol') for x in inputs)), 'pairs':len(pairs),
            'controlled_pair_pixel_validation':'FULL_SOURCE_RECONSTRUCTION' if decode else 'NOT_PERFORMED', 'errors':errors}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--output')
    ap.add_argument('--skip-decode',action='store_true',help='Verify hashes and frozen source/pixel evidence without repeating source image reconstruction')
    args = ap.parse_args()
    result = validate(args.root,decode=not args.skip_decode)
    if args.output:
        Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
