"""Dependence-aware summaries; counts are not independent-sample confidence."""
from collections import defaultdict

def dependence_summary(inputs,gold,pair_report):
    page=defaultdict(list);stimulus=defaultdict(list);all_pairs=pair_report['pairs'];complete=[]
    for r in all_pairs:
        if not r['complete']:continue
        members=r['members'];effect=r['treatment_minus_baseline_exact'];complete.append(effect)
        page[gold[members[0]]['source_binding']['page_group_id']].append(effect)
        stimulus[tuple(sorted(inputs[u]['image_sha256'] for u in members))].append(effect)
    def mean(a):return sum(a)/len(a) if a else None
    return {'selected_pair_count':len(all_pairs),'complete_pair_count':len(complete),'full_pair_both_exact_count':sum(r.get('both_exact',False) for r in all_pairs),
        'full_pair_both_exact_denominator':len(all_pairs),'unique_complete_source_pages':len(page),'unique_complete_visual_stimuli':len(stimulus),
        'pair_weighted_complete_mean_exact_effect':mean(complete),'source_page_macro_complete_mean_exact_effect':mean([mean(x) for x in page.values()]),
        'visual_stimulus_macro_complete_mean_exact_effect':mean([mean(x) for x in stimulus.values()]),
        'effect_direction':'figure_present minus figure_removed','confidence_intervals_claimed':False,
        'interpretation':'Only complete predictions contribute to effect means. Missing predictions remain failures in view scores and the full-pair both-exact denominator. Correlated task views are not independent samples.'}
