"""Deterministic active exports from preserved baseline, source decisions and geometry."""
import sys
sys.dont_write_bytecode=True
import copy,collections,re
from package_io import *
VERSION='1.1.0';EPOCH='REGION_REPAIR_20261111'
BASELINE_SHA='b500bc27b9713bb3594a01119ab386c2ee42d19b751826267033bda2c7d6102a'
BASELINE_FILES_SHA='65743110e8652094d92063f537222d70bb07489805bcf6f9184f8ab3a23392dc'
CONFIRMED='CONFIRMED_AI_REFERENCE';PENDING='PENDING_UNRESOLVED';NEGATIVE='NON_TRANSCRIBABLE_NEGATIVE'
MODEL_KEYS={'sample_uid','legacy_region_id','source_uid','source_sha256','image','image_sha256','width','height','original_crop','original_crop_sha256','task_type','requested_format','scope','locator','input_transform','input_fingerprint'}
PATH_PREFIXES=('images/','original-crops/','sources/','source-pages/','document-inputs/','evidence/')
def rebase(v):
    if isinstance(v,list):return [rebase(x) for x in v]
    if isinstance(v,dict):return {k:rebase(x) for k,x in v.items()}
    if isinstance(v,str) and v.startswith(PATH_PREFIXES):return 'baseline/'+v
    return v
def derive(root):
    base=root/'baseline';need(sha(base/'checksums.sha256')==BASELINE_SHA,'WRONG_BASELINE')
    baseline=index(rows(base/'annotations/records.jsonl'));models=rows(base/'inputs/all-model-inputs.jsonl');M=index(models)
    geometry=index(rows(root/'sources/pdf-geometry.jsonl'));patches=index(rows(root/'review/patches.jsonl'));outmodels=[];records=[];changes=[]
    need(set(geometry)=={m['sample_uid'] for m in models if m['locator']['kind']=='PDF_SOURCE_LINE'},'GEOMETRY_COVERAGE')
    need(set(patches)=={'region-c5b096a23868c994f62ca01c','region-bf622e2ef99b575a78731979','region-70079877c337b6884ae333d7','region-17d63c98bc3a639a2443dcbf'},'PATCH_SCOPE_DIFFERS')
    for patch in patches.values():
        need(patch['before_sha256']==objsha(baseline[patch['sample_uid']]),'PATCH_BEFORE_CHANGED');need(patch['image_sha256']==M[patch['sample_uid']]['image_sha256'],'PATCH_WRONG_IMAGE');need(set(patch['set'])<={'reference_text','annotation_status','observed_partial_text'},'PATCH_PROTECTED_FIELD')
        for evidence in patch['evidence']:safe(root,evidence)
    for old in models:
        uid=old['sample_uid'];m=rebase(copy.deepcopy(old));loc=m['locator']
        if uid in geometry:
            g=geometry[uid];need(g['source_uid']==m['source_uid'] and g['bbox_display_pdf_pt']==old['locator']['bbox_pdf_pt'],'GEOMETRY_TARGET_CHANGED')
            loc.update(bbox_coordinate_space='ORIGINAL_PDF_PAGE_DISPLAY_POINTS',bbox_pdf_unrotated_pt=g['bbox_unrotated_pdf_pt'],page_rotation=g['page_rotation'],page_rect_display_pt=g['page_rect_display_pt'],page_rect_unrotated_pt=g['page_rect_unrotated_pt'])
        else:loc['source_media_key']=m['source_uid']+':'+loc['media_part']
        m.pop('input_fingerprint');m['input_fingerprint']=objsha(m);outmodels.append(m)
        a=rebase(copy.deepcopy(baseline[uid]));a['input_fingerprint']=m['input_fingerprint'];a['review_epoch']=EPOCH;a['baseline_input_fingerprint']=old['input_fingerprint']
        a['review_reason']='Inherited source-bound AI reference; not newly visually recertified.';a['uncertain_spans']=[]
        if uid in patches:
            p=patches[uid];a.update(p['set']);a['review_reason']=p['rationale'];a['source_review']={'method':'SOURCE_BOUND_REPAIR_NOT_NEW_BLIND_REVIEW','artifact':p['evidence'][0],'artifact_sha256':sha(safe(root,p['evidence'][0])),'baseline_source_review':rebase(baseline[uid]['source_review']),'execution_proof_level':'LOCAL_SOURCE_CONTEXT_OR_PRIOR_VISUAL_RECHECK','exact_historical_tool_trace_certified':False}
        if a['annotation_status']!=CONFIRMED:
            a['content_type_basis']=a['annotation_status']+'_METADATA_NOT_CONFIRMED_TRANSCRIPTION'
            if a['annotation_status']==PENDING:a['uncertain_spans']=[a['review_reason'] if uid in patches else 'Inherited unresolved glyph/content; see source_review artifact.']
        # Preserve the O/0 observation instead of an empty partial-transcription field.
        if a['legacy_region_id']=='source-e5884a9dbdd807a4:media:028':
            a['observed_partial_text']='[O/0]';a['visible_evidence']='历史b03记录为倾斜闭合椭圆单字形；这里只保存O/0未决，不据轮廓猜字符。'
        else:a['visible_evidence']=None
        # A batch is corroborating only for the same actual target and a resolved matching reference.
        proof=a['source_review'];prior_cor=proof.pop('corroborating_batches',[]);accepted=[];history=[]
        for bid in prior_cor:
            item=next(x for x in read(base/'evidence/new-review'/bid/'review.json')['items'] if x['region_id']==a['legacy_region_id'])
            same=item.get('target_image_sha256')==m['image_sha256'] and item.get('status') in {'CONFIRMED','RESOLVED'}
            if same:
                from text_metrics import narrow
                same=narrow(item['original_text'])==narrow(a['reference_text'] or '')
            if same:accepted.append(bid)
            else:history.append({'batch_id':bid,'status':item['status'],'target_image_sha256':item.get('target_image_sha256'),'relation':'HISTORICAL_DIFFERENT_INPUT_OR_UNRESOLVED_NOT_CORROBORATION'})
        proof['corroborating_batches']=accepted;proof['historical_noncorroborating_batches']=history
        artifact=read(safe(root,proof['artifact']));proof['artifact_kind']='SOURCE_CONTEXT_REVIEW' if artifact.get('method')=='SOURCE_CONTEXT_REVIEW_NOT_BLIND' else ('REVIEW_ITEMS_OR_VISUAL_FINDINGS' if 'items' in artifact else 'EXECUTION_RECEIPT_WITH_TASK_RESULT_BINDINGS')
        proof['current_execution_scope']='CURRENT_SOURCE_BOUND_PATCH' if uid in patches else 'INHERITED_ARCHIVE_NOT_REEXECUTED'
        records.append(a)
        if uid in patches:changes.append({'sample_uid':uid,'old_record_sha256':objsha(baseline[uid]),'old_reference_text':baseline[uid]['reference_text'],'new_reference_text':a['reference_text'],'old_status':baseline[uid]['annotation_status'],'new_status':a['annotation_status'],'evidence':patches[uid]['evidence'],'reason':patches[uid]['rationale']})
    A=index(records);MM=index(outmodels);gold=[a for a in records if a['annotation_status']==CONFIRMED];pending=[a for a in records if a['annotation_status']==PENDING];negative=[a for a in records if a['annotation_status']==NEGATIVE]
    groups=collections.defaultdict(list)
    for g in gold:groups[MM[g['sample_uid']]['image_sha256']].append(g['sample_uid'])
    keep={sorted(v)[0] for v in groups.values()};default=[a for a in gold if a['sample_uid'] in keep]
    sources=rebase(rows(base/'sources/source-manifest.jsonl'));coverage=[];source_inventory=index(rows(root/'sources/source-inventory.jsonl'),'source_uid')
    need(set(source_inventory)=={s['source_uid'] for s in sources},'SOURCE_INVENTORY_COVERAGE')
    for original in rows(base/'sources/source-coverage.jsonl'):
        c=rebase(copy.deepcopy(original));sid=c['source_uid'];subset=[a for a in records if a['source_uid']==sid];source=next(s for s in sources if s['source_uid']==sid)
        inv=source_inventory[sid];need(inv['source_sha256']==source['sha256'],'SOURCE_INVENTORY_IDENTITY')
        selected_pages=sorted({m['locator']['original_page_index_0'] for m in outmodels if m['source_uid']==sid and m['locator']['kind']=='PDF_SOURCE_LINE'});selected_media=sorted({m['locator']['media_part'] for m in outmodels if m['source_uid']==sid and 'media_part' in m['locator']})
        need(set(selected_media)<=set(inv['bitmap_media_parts']) and all(0<=p<(inv['total_original_pages'] or 0) for p in selected_pages),'SELECTED_TARGET_OUTSIDE_SOURCE_INVENTORY')
        c.update(kind=source['kind'],annotation_status_counts=dict(collections.Counter(a['annotation_status'] for a in subset)),regions=len(subset),sample_uids=[a['sample_uid'] for a in subset],coverage_epoch=EPOCH,count_unit='UNIQUE_REGIONS_WITHIN_SOURCE',total_original_pages=inv['total_original_pages'],selected_original_pages_0=selected_pages,unreviewed_original_pages_0=sorted(set(range(inv['total_original_pages'] or 0))-set(selected_pages)),total_original_bitmap_media=len(inv['bitmap_media_parts']) if source['kind']=='DOCX' else None,selected_bitmap_media=len(selected_media) if source['kind']=='DOCX' else None,selected_bitmap_media_parts=selected_media,not_selected_bitmap_media_parts=sorted(set(inv['bitmap_media_parts'])-set(selected_media)),non_bitmap_media_parts=inv['non_bitmap_media_parts']);coverage.append(c)
    docs=rebase(rows(base/'inputs/document-model-inputs.jsonl'))
    for doc in docs:doc['target_input_manifest']='inputs/all-model-inputs.jsonl';doc['target_input_manifest_sha256']=objsha(outmodels);doc['input_schema_version']=VERSION
    prior=read(base/'evidence/reused-source-review/provenance.json')['source_records'];provenance=[]
    for original in prior:
        p=rebase(copy.deepcopy(original));a=next(a for a in records if a['legacy_region_id']==p['region_id']);p['current_sample_uid']=a['sample_uid'];p['historical_artifact_status']='INHERITED_ACTIVE' if a['source_review']['method'].startswith('REUSED') else 'SUPERSEDED';p['superseded_by']=a['source_review']['artifact'] if p['historical_artifact_status']=='SUPERSEDED' else None;p['artifact_kind']='REVIEW_ITEMS_OR_VISUAL_FINDINGS' if 'items' in read(safe(root,p['artifact'])) else 'EXECUTION_RECEIPT_WITH_TASK_RESULT_BINDINGS';provenance.append(p)
    migration=[]
    for old in models:
        uid=old['sample_uid'];m=MM[uid];migration.append({'sample_uid':uid,'legacy_region_id':old['legacy_region_id'],'baseline_input_fingerprint':old['input_fingerprint'],'current_input_fingerprint':m['input_fingerprint'],'identity_policy':'UID_PRESERVED_UNCHANGED_TARGET_PIXELS_LOCATOR_SEMANTICS_CORRECTED','old_record':'baseline/annotations/records.jsonl','current_image':m['image'],'current_original_crop':m['original_crop'],'image_sha256':m['image_sha256'],'original_crop_sha256':m['original_crop_sha256']})
    freeze=[{'sample_uid':m['sample_uid'],'input_fingerprint':m['input_fingerprint'],'image_sha256':m['image_sha256'],'source_sha256':m['source_sha256']} for m in outmodels]
    return {'annotations/records.jsonl':records,'annotations/gold.jsonl':gold,'annotations/default-gold.jsonl':default,'annotations/pending.jsonl':pending,'annotations/negative-controls.jsonl':negative,'inputs/all-model-inputs.jsonl':outmodels,'inputs/scorable-model-inputs.jsonl':[m for m in outmodels if m['sample_uid'] in index(gold)],'inputs/default-model-inputs.jsonl':[m for m in outmodels if m['sample_uid'] in keep],'inputs/document-model-inputs.jsonl':docs,'sources/source-manifest.jsonl':sources,'sources/source-coverage.jsonl':coverage,'annotations/revisions.jsonl':changes,'migration/old-to-new.jsonl':migration,'evidence/provenance.jsonl':provenance,'evidence/task-input-freeze.jsonl':freeze,'annotations/duplicate-groups.jsonl':[{'image_sha256':h,'sample_uids':uids} for h,uids in groups.items() if len(uids)>1]}
def summary(data):
    a=data['annotations/records.jsonl'];g=data['annotations/gold.jsonl']
    return {'version':VERSION,'epoch':EPOCH,'regions':len(a),'active':len(g),'default':len(data['annotations/default-gold.jsonl']),'pending':len(data['annotations/pending.jsonl']),'negative':len(data['annotations/negative-controls.jsonl']),'critical_all':sum(x['critical'] for x in a),'critical_active':sum(x['critical'] for x in g),'content_type_all':dict(collections.Counter(x['content_type'] for x in a)),'content_type_active':dict(collections.Counter(x['content_type'] for x in g)),'human_verified':False,'new_model_calls':0,'whole_population_visually_recertified':False,'independent_new_blind_contexts':0,'historical_context_claim_scope':'Recorded IDs and hashes self-consistent; external historic rollout authenticity not newly certified.'}
def descriptor(data):
    q=summary(data)
    return {'name':'文档区域转录标准评测集','version':VERSION,'epoch':EPOCH,'entry_doc':'README.md','baseline_root':'baseline','baseline_checksums_sha256':BASELINE_SHA,'population_regions':q['regions'],'source_documents':4,'pdf_sampled_pages':20,'active_eligible_samples':q['active'],'default_eligible_samples':q['default'],'pending_samples':q['pending'],'negative_controls':q['negative'],'critical_active_samples':q['critical_active'],'model_inputs':'inputs/scorable-model-inputs.jsonl','active_gold':'annotations/gold.jsonl','default_model_inputs':'inputs/default-model-inputs.jsonl','default_gold':'annotations/default-gold.jsonl','all_records':'annotations/records.jsonl','all_model_inputs':'inputs/all-model-inputs.jsonl','pending':'annotations/pending.jsonl','negative_gold':'annotations/negative-controls.jsonl','document_model_inputs':'inputs/document-model-inputs.jsonl','source_manifest':'sources/source-manifest.jsonl','source_coverage':'sources/source-coverage.jsonl','validator':'tools/validate_dataset.py','scorer':'tools/score_direct_transcription.py','human_verified':False,'historically_exposed_not_new_holdout':True,'historical_scores_recomputed':False,'scoring_claim':'EXPLICIT_CURRENT_POSITIVE_CROP_VIEW_NOT_FULL_TEXT','protocol':'DIRECT_CROP_AND_SOURCE_LOCATED_DOCUMENT_REGIONS_V11'}
def verify_exports(root):
    verify_baseline(root)
    data=derive(root)
    for rel,expected in data.items():need(rows(root/rel)==expected,'CURRENT_EXPORT_DIFFERS:'+rel)
    need(read(root/'dataset.json')==descriptor(data),'DESCRIPTOR_DIFFERS')
    need(read(root/'quality/current.json')==summary(data),'QUALITY_SUMMARY_DIFFERS')
    return data
def verify_baseline(root):
    need(sha(root/'baseline/checksums.sha256')==BASELINE_SHA,'WRONG_BASELINE')
    need(sha(root/'review/baseline-files.json')==BASELINE_FILES_SHA,'BASELINE_FREEZE_CHANGED')
    frozen=read(root/'review/baseline-files.json');actual={p.relative_to(root/'baseline').as_posix() for p in (root/'baseline').rglob('*') if p.is_file()};need(set(frozen)==actual,'BASELINE_INVENTORY_CHANGED')
    for rel,meta in frozen.items():need(sha(safe(root,'baseline/'+rel))==meta['sha256'],'BASELINE_FILE_CHANGED:'+rel)
    return len(frozen)
