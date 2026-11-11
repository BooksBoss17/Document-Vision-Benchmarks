"""Evaluate two explicitly supplied text files using the report's frozen content metric."""
from pathlib import Path
import argparse,json,sys
sys.dont_write_bytecode=True
from ekb_bench import verify_release

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-file',type=Path,required=True)
    parser.add_argument('--prediction-file',type=Path,required=True)
    parser.add_argument('--format',choices=['TEXT','LATEX','TABLE_HTML'],required=True)
    parser.add_argument('--output',type=Path,required=True);a=parser.parse_args()
    verify_release()
    if a.output.resolve() in {a.reference_file.resolve(),a.prediction_file.resolve()}:parser.error('output must not overwrite an input')
    sys.path.insert(0,str(Path(__file__).resolve().parent/'vendor/report-content-metrics'))
    from content_score_c06 import normalize,alignment
    reference=normalize(a.reference_file.read_text('utf8'),a.format)
    prediction=normalize(a.prediction_file.read_text('utf8'),a.format)
    result=alignment(reference['units'],prediction['units'])
    r,p,c=(result[k] for k in ['reference_units','predicted_units','correct'])
    result.update(protocol='CONTENT_CHAR_EQUIV_V3_PLUS_TABLE_SYNTAX_V2_EXACT_DP',scope='EXPLICIT_TEXT_FILES_NOT_OFFICIAL_BENCHMARK_SCORE',precision=c/max(1,p),recall=c/max(1,r),F1=2*c/max(1,r+p),CER=result['edits']/max(1,r),reference_supported=reference['supported'],prediction_supported=prediction['supported'],reference_warnings=reference['warnings'],prediction_warnings=prediction['warnings'],model_execution=False)
    with a.output.open('x',encoding='utf8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps(result,ensure_ascii=False));return 0

if __name__=='__main__':sys.exit(main())
