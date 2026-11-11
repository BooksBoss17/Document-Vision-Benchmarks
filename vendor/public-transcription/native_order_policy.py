"""Only native undefined auxiliary order is exempt; every block stays content."""
import hashlib,json,copy
AUXILIARY={'header','page_number','footer','page_footnote'}
def digest(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()
def without_order(i,g):
    i=copy.deepcopy(i);g=copy.deepcopy(g);i['task'].pop('unordered_region_ids',None);g.pop('unordered_region_ids',None)
    return digest({'input':i,'gold':g})
def need(ok,msg):
    if not ok:raise ValueError('native order policy: '+msg)
def auxiliary_none_ids(legacy_uid,selected_ids,page):
    annotations={a['anno_id']:a for a in page['layout_dets']};out=[]
    for aid in selected_ids:
        need(aid in annotations,'native selected annotation missing')
        a=annotations[aid]
        if a.get('order') is None and a.get('category_type') in AUXILIARY:
            out.append('r-'+hashlib.sha256((legacy_uid+':'+str(aid)).encode('utf-8')).hexdigest()[:20])
    return sorted(out)
def validate_native_order(row,gold,old_row,old_gold,entry,page):
    need(row['protocol']=='PAGE_TRANSCRIPTION' and old_gold['review_status']=='SOURCE_VERIFIED' and old_gold['source_binding']['dataset']=='omnidocbench','outside source-native PAGE scope')
    need(without_order(row,gold)==entry['nonorder_input_gold_sha256'],'content or nonorder task changed beyond native-order adjudication')
    need(page['page_info']['image_path']==entry['native_page_image'],'wrong native page')
    need(old_gold['source_binding']['annotation_id']==entry['selected_annotation_ids'],'native target annotation selection changed')
    prior=old_row['task'].get('unordered_region_ids',[]);need(prior==entry['prior_unordered_region_ids'],'existing unordered policy changed')
    desired=sorted(set(prior)|set(auxiliary_none_ids(row['legacy_uid'],entry['selected_annotation_ids'],page)))
    need(set(desired)<={r['region_id'] for r in row['task']['target_regions']},'native auxiliary region is not a requested target')
    need(row['task']['unordered_region_ids']==desired and gold['unordered_region_ids']==desired,'undefined auxiliaries or known body order incorrectly exempted')
    need(sorted(set(desired)-set(prior))==entry['additional_unordered_region_ids'],'additional exempt list differs from native None authority')
