"""Self-contained adversarial tests; private fixture is copied under explicit external --work."""
import sys
sys.dont_write_bytecode=True
import argparse,unittest,tempfile,shutil,random,subprocess,contextlib,io,time
from package_io import *
from recipe import *
from score_direct_transcription import main as score,measure,prepare_inputs,predictions_for
from text_metrics import narrow,distance
PACKAGE=Path(__file__).resolve().parents[1]
WORK=None
def dp(a,b):
    p=list(range(len(b)+1))
    for i,c in enumerate(a,1):
        q=[i]
        for j,d in enumerate(b,1):q.append(min(q[-1]+1,p[j]+1,p[j-1]+(c!=d)))
        p=q
    return p[-1]
class Contracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.container=tempfile.TemporaryDirectory(dir=WORK);cls.base=Path(cls.container.name);cls.root=cls.base/'dataset';shutil.copytree(PACKAGE,cls.root,ignore=shutil.ignore_patterns('__pycache__') if not (PACKAGE/'baseline/tools/__pycache__').exists() else None)
        cls.originals={};cls.manifest=(cls.root/'checksums.sha256').read_bytes()
    @classmethod
    def tearDownClass(cls):
        need(cls.base.resolve().is_relative_to(WORK.resolve()),'UNSAFE_TEST_CLEANUP');cls.container.cleanup()
    def setUp(self):self.backups={}
    def tearDown(self):
        for p,raw in self.backups.items():p.write_bytes(raw)
        (self.root/'checksums.sha256').write_bytes(self.manifest)
    def change(self,rel,mutate):
        p=self.root/rel;self.backups.setdefault(p,p.read_bytes());value=rows(p) if rel.endswith('.jsonl') else read(p);mutate(value)
        if rel.endswith('.jsonl'):lines(p,value)
        else:write(p,value)
    def reject_export(self,rel,mutate):
        self.change(rel,mutate);seal_package(self.root)
        with self.assertRaises(ValueError):verify_exports(self.root)
    def predictions(self):
        G=index(rows(self.root/'annotations/default-gold.jsonl'));M=rows(self.root/'inputs/default-model-inputs.jsonl');p=self.base/(self._testMethodName+'.jsonl');lines(p,[{'sample_uid':m['sample_uid'],'input_fingerprint':m['input_fingerprint'],'status':'SUCCESS','text':G[m['sample_uid']]['reference_text']} for m in M]);return p
    def test_01_positive_exports(self):self.assertEqual(len(verify_exports(self.root)['annotations/records.jsonl']),200)
    def test_02_default_reference_tamper(self):self.reject_export('annotations/default-gold.jsonl',lambda xs:xs[0].update(reference_text='TAMPERED'))
    def test_03_revisions_tamper(self):self.reject_export('annotations/revisions.jsonl',lambda xs:xs[0].update(new_reference_text='TAMPERED'))
    def test_04_migration_tamper(self):self.reject_export('migration/old-to-new.jsonl',lambda xs:xs.clear())
    def test_05_freeze_tamper(self):self.reject_export('evidence/task-input-freeze.jsonl',lambda xs:xs[0].update(input_fingerprint='0'*64))
    def test_06_coverage_tamper(self):self.reject_export('sources/source-coverage.jsonl',lambda xs:xs[0].update(annotation_status_counts={'BOGUS':999}))
    def test_07_duplicate_groups_tamper(self):self.reject_export('annotations/duplicate-groups.jsonl',lambda xs:xs.append({'image_sha256':'fake','sample_uids':['fake']}))
    def test_08_input_new_key_even_rehashed(self):
        def mutate(xs):
            xs[0]['hint_sentence']='answer';xs[0]['input_fingerprint']=objsha({k:v for k,v in xs[0].items() if k!='input_fingerprint'})
        self.reject_export('inputs/all-model-inputs.jsonl',mutate)
    def test_09_pending_promotion(self):self.reject_export('annotations/pending.jsonl',lambda xs:xs[0].update(annotation_status=CONFIRMED,reference_text='O'))
    def test_10_critical_false(self):self.reject_export('annotations/records.jsonl',lambda xs:[x.update(critical=False) for x in xs])
    def test_11_descriptor_critical(self):self.reject_export('dataset.json',lambda d:d.update(critical_active_samples=0))
    def test_12_false_corroboration(self):self.reject_export('annotations/records.jsonl',lambda xs:next(x for x in xs if x['legacy_region_id'].endswith('p09:line2'))['source_review'].update(corroborating_batches=['b01']))
    def test_13_coordinate_revert(self):self.reject_export('inputs/all-model-inputs.jsonl',lambda xs:next(x for x in xs if x['locator']['kind']=='PDF_SOURCE_LINE')['locator'].update(bbox_coordinate_space='ORIGINAL_PDF_PAGE_UNROTATED_POINTS'))
    def test_14_rotation_false(self):
        from validate_dataset import validate
        self.change('sources/pdf-geometry.jsonl',lambda xs:next(x for x in xs if x['page_rotation']==180).update(page_rotation=0));data=derive(self.root)
        # Keep derivable files consistent, so the real page comparison must reject it.
        for rel,xs in data.items():
            p=self.root/rel;self.backups.setdefault(p,p.read_bytes());lines(p,xs)
        seal_package(self.root)
        with self.assertRaisesRegex(ValueError,'ROTATION'):validate(self.root)
    def test_15_normalization_preserves_arrows(self):self.assertNotEqual(narrow(r'A\leftarrow B'),narrow(r'A\rightarrow B'))
    def test_16_normalization_math_delimiters(self):
        self.assertEqual(narrow(r'$x_1$'),narrow(r'\(x_1\)'));self.assertEqual(narrow(r'\left(x\right)'),'(x)')
        for a,b in [('x²','x2'),('x_1','x1'),('A','a'),('-Q','Q'),('第6页','第3页')]:self.assertNotEqual(narrow(a),narrow(b))
    def test_17_distance_against_independent_dp(self):
        rng=random.Random(433)
        for _ in range(500):
            a=''.join(rng.choice('ab壹²_→') for _ in range(rng.randrange(25)));b=''.join(rng.choice('ab壹²_→') for _ in range(rng.randrange(25)));self.assertEqual(distance(a,b),dp(a,b))
    def test_18_distance_long_exact(self):self.assertEqual(distance('x'*6000,'x'*5999+'y'),1)
    def test_19_success_review_failed_missing(self):
        M=rows(self.root/'inputs/default-model-inputs.jsonl');G=rows(self.root/'annotations/default-gold.jsonl');expected=index(M);GB=index(G);pred={m['sample_uid']:{'status':'SUCCESS','text':GB[m['sample_uid']]['reference_text']} for m in M[:-3]}
        pred[M[-3]['sample_uid']]={'status':'REVIEW','text':'ignored'};pred[M[-2]['sample_uid']]={'status':'FAILED','text':''};_,result,_=measure(G,expected,pred)
        self.assertEqual(result['state_counts'],{'SUCCESS':len(M)-3,'REVIEW':1,'FAILED':1,'MISSING':1});self.assertEqual(result['CER_measured_only'],0);self.assertGreater(result['CER_full_view_empty_output_penalty'],0)
    def test_20_duplicate_gold_rejected(self):
        G=rows(self.root/'annotations/default-gold.jsonl');M=index(rows(self.root/'inputs/default-model-inputs.jsonl'))
        with self.assertRaisesRegex(ValueError,'DUPLICATE_ID'):measure(G+[G[0]],M,{})
    def test_21_duplicate_input_rejected(self):
        self.change('inputs/default-model-inputs.jsonl',lambda xs:xs.append(xs[0]))
        with self.assertRaisesRegex(ValueError,'DUPLICATE_ID'):prepare_inputs(self.root,'default')
    def test_22_empty_view_rejected(self):
        self.change('inputs/default-model-inputs.jsonl',lambda xs:xs.clear())
        with self.assertRaisesRegex(ValueError,'EMPTY_SCORE_VIEW'):prepare_inputs(self.root,'default')
    def test_23_path_escape_rejected(self):
        self.change('dataset.json',lambda d:d.update(default_gold='../outside.jsonl'))
        with self.assertRaisesRegex(ValueError,'UNSAFE'):prepare_inputs(self.root,'default')
    def test_24_missing_uid_friendly_error(self):
        p=self.base/'bad-uid.jsonl';lines(p,[{'status':'SUCCESS','text':'x','input_fingerprint':'x'}])
        with self.assertRaisesRegex(ValueError,'INVALID_OR_DUPLICATE_ID'):predictions_for(p,index(rows(self.root/'inputs/default-model-inputs.jsonl')))
    def test_25_prediction_wrong_fingerprint(self):
        p=self.predictions();xs=rows(p);xs[0]['input_fingerprint']='0'*64;lines(p,xs)
        with self.assertRaisesRegex(ValueError,'WRONG_INPUT_FINGERPRINT'):predictions_for(p,index(rows(self.root/'inputs/default-model-inputs.jsonl')))
    def test_26_prediction_duplicate(self):
        p=self.predictions();xs=rows(p);lines(p,xs+[xs[0]])
        with self.assertRaisesRegex(ValueError,'DUPLICATE_ID'):predictions_for(p,index(rows(self.root/'inputs/default-model-inputs.jsonl')))
    def test_27_synthetic_roundtrip(self):
        result=score(self.root,self.predictions(),self.base/'roundtrip','default');self.assertEqual(result['literal_correct'],195);self.assertEqual(result['CER_measured_only'],0)
    def test_28_no_image_dependency_for_score(self):
        p=self.predictions();proc=subprocess.run([sys.executable,'-S','-B',str(self.root/'tools/score_direct_transcription.py'),'--root',str(self.root),'--predictions',str(p),'--output',str(self.base/'stdlib-score')],capture_output=True,text=True,encoding='utf8');self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
    def test_29_runtime_cache_does_not_break_payload(self):
        p=self.root/'tools/__pycache__/extra.pyc';p.parent.mkdir(exist_ok=True);p.write_bytes(b'not payload')
        try:self.assertGreater(verify_manifest(self.root),0)
        finally:p.unlink()
    def test_30_import_cache_does_not_change_payload(self):
        before=inventory(self.root);proc=subprocess.run([sys.executable,'-c','import sys;sys.path.insert(0,'+repr(str(self.root/'tools'))+');import score_direct_transcription'],capture_output=True,text=True);self.assertEqual(proc.returncode,0,proc.stderr);self.assertEqual(inventory(self.root),before);self.assertGreater(verify_manifest(self.root),0)
    def test_31_baseline_geometry_file_protected(self):
        self.change('sources/pdf-geometry.jsonl',lambda xs:xs[0].update(bbox_unrotated_pdf_pt=[0.0,0.0,1.0,1.0]))
        for rel,xs in derive(self.root).items():
            p=self.root/rel;self.backups.setdefault(p,p.read_bytes());lines(p,xs)
        seal_package(self.root)
        from validate_dataset import validate
        with self.assertRaisesRegex(ValueError,'ROUNDTRIP'):validate(self.root)
    def test_32_cli_without_B_does_not_create_cache(self):
        p=self.predictions();before={p.relative_to(self.root).as_posix() for p in self.root.rglob('*.pyc')};proc=subprocess.run([sys.executable,str(self.root/'tools/score_direct_transcription.py'),'--root',str(self.root),'--predictions',str(p),'--output',str(self.base/'cli-default')],capture_output=True,text=True,encoding='utf8');self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr);self.assertEqual({p.relative_to(self.root).as_posix() for p in self.root.rglob('*.pyc')},before)
    def test_33_baseline_freeze_cannot_be_rewritten(self):
        self.change('review/baseline-files.json',lambda d:d.pop(next(iter(d))))
        with self.assertRaisesRegex(ValueError,'BASELINE_FREEZE_CHANGED'):verify_exports(self.root)
    def test_34_fake_source_page_count_rejected_by_actual_pdf(self):
        self.change('sources/source-inventory.jsonl',lambda xs:next(x for x in xs if x['kind']=='PDF').update(total_original_pages=999))
        for rel,xs in derive(self.root).items():
            p=self.root/rel;self.backups.setdefault(p,p.read_bytes());lines(p,xs)
        seal_package(self.root)
        from validate_dataset import validate
        with self.assertRaisesRegex(ValueError,'SOURCE_PAGE_INVENTORY_DIFFERS'):validate(self.root)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=PACKAGE);p.add_argument('--work',type=Path,required=True);args,extra=p.parse_known_args();PACKAGE=args.root.resolve();WORK=args.work.resolve();need(not WORK.is_relative_to(PACKAGE),'TEST_WORK_MUST_BE_EXTERNAL');need(not WORK.exists(),'FRESH_TEST_WORK_REQUIRED');WORK.mkdir(parents=True);sys.argv=[sys.argv[0]]+extra;unittest.main(verbosity=2)
