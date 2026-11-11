"""Four-class and retention metrics; missing/unresolved never count as correct."""
from collections import defaultdict
from core import CLASSES
def metrics(gold,pred):
 matrix={a:{b:0 for b in CLASSES+['MISSING','UNRESOLVED']} for a in CLASSES};correct=present=0;false_exclusions=[];false_retentions=[];unresolved=[];necessary=other=tp=tn=0
 for g in gold:
  uid=g['sample_id'];p=pred.get(uid,'MISSING');label=g['label'];matrix[label][p]+=1;correct+=p==label;present+=p!='MISSING';positive=label!='decoration';necessary+=positive;other+=not positive
  if p in ['MISSING','UNRESOLVED']:unresolved.append(uid)
  elif p=='decoration':
   if positive:false_exclusions.append(uid)
   else:tn+=1
  elif positive:tp+=1
  else:false_retentions.append(uid)
 per={}
 for c in CLASSES:
  t=matrix[c][c];support=sum(matrix[c].values());p=sum(matrix[x][c] for x in CLASSES);precision=t/p if p else 0;recall=t/support if support else 0;per[c]={'support':support,'precision':precision,'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0}
 n=len(gold);supported=[v['f1'] for v in per.values() if v['support']]
 return {'n':n,'present_predictions':present,'correct':correct,'accuracy':correct/n if n else None,'coverage':present/n if n else None,'macro_f1_fixed_four':sum(v['f1'] for v in per.values())/4 if n else None,'macro_f1_supported_classes':sum(supported)/len(supported) if supported else None,'per_class':per,'confusion_matrix':matrix,'retention':{'necessary_count':necessary,'miscellaneous_count':other,'necessary_content_recall':tp/necessary if necessary else None,'miscellaneous_exclusion_rate':tn/other if other else None,'binary_correct':tp+tn,'binary_accuracy':(tp+tn)/n if n else None,'false_exclusion_count':len(false_exclusions),'false_retention_count':len(false_retentions),'unresolved_or_missing_count':len(unresolved),'false_exclusions':false_exclusions,'false_retentions':false_retentions,'unresolved_or_missing':unresolved,'unresolved_counts_as_correct':False}}
def report(gold,pred,records,near_clusters):
 bybook=defaultdict(list);byorigin=defaultdict(list);byimage=defaultdict(list);bypage=defaultdict(list);bynear=defaultdict(list);record={r['sample_id']:r for r in records};near={u:c['cluster_id'] for c in near_clusters for u in c['sample_ids']}
 for g in gold:
  r=record[g['sample_id']];bybook[g['book_code']].append(g);byorigin[g['label_origin']].append(g);byimage[r['sha256']].append(g);bypage[(r['source_pdf_sha256'],r['source_pdf_page_number'])].append(g);bynear[near.get(g['sample_id'],'singleton:'+g['sample_id'])].append(g)
 def grouped(values):return {str(k):metrics(v,pred) for k,v in sorted(values.items())}
 def macro(groups):
  values=[sum(pred.get(g['sample_id'])==g['label'] for g in v)/len(v) for v in groups.values()];return {'groups':len(values),'macro_group_accuracy':sum(values)/len(values) if values else None,'sample_views':sum(map(len,groups.values()))}
 return {'all':metrics(gold,pred),'core':metrics([g for g in gold if not g['boundary_case']],pred),'boundary':metrics([g for g in gold if g['boundary_case']],pred),'historical_user_adjudications':metrics([g for g in gold if g['explicit_historical_user_adjudication']],pred),'v12_curated':metrics([g for g in gold if g['v12_curated']],pred),'by_book':grouped(bybook),'by_label_origin':grouped(byorigin),'dependence':{'exact_image_groups':macro(byimage),'primary_source_page_groups':macro(bypage),'near_threshold_groups':macro(bynear),'independent_sample_confidence_interval_claimed':False,'near_groups_are_diagnostic_not_semantic_truth':True}}
