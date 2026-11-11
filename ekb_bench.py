"""Run the original benchmark protocols with explicit, separately licensed data."""
from pathlib import Path
import argparse,hashlib,importlib.metadata,json,os,runpy,sys
sys.dont_write_bytecode=True
for stream in (sys.stdout,sys.stderr):
    if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
ROOT=Path(__file__).resolve().parent

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def catalog():return json.loads((ROOT/'benchmarks.json').read_text('utf8'))['benchmarks']

def pinned_data(name,path):
    root=Path(path).resolve();entry=next(x for x in catalog() if x['id']==name)
    manifest=root/'checksums.sha256'
    if not manifest.is_file():raise ValueError('Supply the separately authorized complete dataset package: '+name)
    if sha(manifest)!=entry['source_package_checksums_sha256']:raise ValueError('Dataset release identity differs: '+name)
    return root

def pinned_member(root,relative):
    entries={}
    for line in (root/'checksums.sha256').read_text('utf8').splitlines():
        digest,name=line.split('  ',1)
        if name in entries:raise ValueError('Duplicate dataset manifest member')
        entries[name]=digest
    member=(root/relative).resolve()
    if relative not in entries or not member.is_relative_to(root) or not member.is_file() or sha(member)!=entries[relative]:
        raise ValueError('Pinned dataset member missing or changed: '+relative)
    return member

def verify_release():
    p=ROOT/'release-manifest.json'
    if not p.is_file():raise ValueError('The public release is not frozen: release-manifest.json missing')
    data=json.loads(p.read_text('utf8'))
    if data.get('schema')!='ekb-public-release-manifest-v1':raise ValueError('Unsupported public release manifest')
    for row in data['files']:
        rel=Path(row['path']);actual=(ROOT/rel).resolve()
        if rel.is_absolute() or '..' in rel.parts or not actual.is_relative_to(ROOT) or not actual.is_file() or sha(actual)!=row['sha256']:
            raise ValueError('Public release file missing or changed: '+str(rel))
    return len(data['files'])

def dispatch(name,action,data_root,parent_root,dependencies,extra):
    verify_release();root=pinned_data(name,data_root)
    if dependencies:os.environ['EKB_BENCH_DEPENDENCIES']=str(Path(dependencies).resolve())
    tool_family=name;prefix=[];root_flag=True
    if name=='image-classification':script='cli.py';prefix=[action]
    elif name=='transcription-crops':
        script='validate_dataset.py' if action in {'verify','validate'} else 'score.py';prefix=[] if script.startswith('validate') else [action]
        if action not in {'verify','validate','seal','score'}:raise ValueError('This dataset supports verify/validate/seal/score')
    elif name=='document-regions':
        if action not in {'verify','validate','score','test'}:raise ValueError('This dataset supports verify/validate/score/test')
        script='test_contracts.py' if action=='test' else 'validate_dataset.py' if action in {'verify','validate'} else 'score_direct_transcription.py'
    else:
        if name=='public-quick':
            selection=pinned_member(root,'selection.json')
            if parent_root is None:raise ValueError('public-quick requires --parent-root with its complete v2.4 parent')
            parent=pinned_data('public-transcription',parent_root)
            if action in {'verify','validate'}:
                script='quick_dataset.py';prefix=['validate'];extra=['--parent',str(parent),*extra]
            else:
                if '--selection' in extra:raise ValueError('public-quick fixes its original complete 480-view selection')
                if action in {'prepare','score'}:extra=['--selection',str(selection),*extra]
                root=parent;tool_family='public-transcription'
        if tool_family=='public-transcription':
            if action=='verify':
                sys.path.insert(0,str(ROOT/'vendor/public-transcription'))
                from package_integrity import verify_package
                print(json.dumps(dict(passed=True,package=verify_package(root)),ensure_ascii=False));return 0
            if action=='validate':script='validate.py'
            elif action in {'prepare','bind'}:script='prepare_run.py';prefix=[action];root_flag=action!='bind'
            elif action=='score':script='scoring.py'
            else:raise ValueError('Public transcription supports verify/validate/prepare/bind/score')
    tool=ROOT/'vendor'/tool_family/script
    sys.path.insert(0,str(tool.parent));sys.argv=[str(tool),*prefix,*(['--root',str(root)] if root_flag else []),*extra]
    runpy.run_path(str(tool),run_name='__main__');return 0

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True);sub.add_parser('list');sub.add_parser('doctor')
    p=sub.add_parser('run');p.add_argument('dataset',choices=[x['id'] for x in catalog()]);p.add_argument('action');p.add_argument('--data-root',required=True);p.add_argument('--parent-root');p.add_argument('--dependencies')
    a,extra=parser.parse_known_args(argv)
    try:
        if a.command=='list':
            if extra:parser.error('Unexpected arguments')
            print(json.dumps(catalog(),ensure_ascii=False,indent=2));return 0
        if a.command=='doctor':
            if extra:parser.error('Unexpected arguments')
            dependencies={}
            for name in ['Pillow','pypdf','PyMuPDF']:
                try:dependencies[name]=importlib.metadata.version(name)
                except importlib.metadata.PackageNotFoundError:dependencies[name]=None
            print(json.dumps(dict(python=sys.version.split()[0],platform=sys.platform,release_files_checked=verify_release(),optional_validation_dependencies=dependencies,model_loading=False),ensure_ascii=False,indent=2));return 0
        if extra[:1]==['--']:extra=extra[1:]
        return dispatch(a.dataset,a.action,a.data_root,a.parent_root,a.dependencies,extra)
    except (OSError,ValueError,KeyError,ImportError) as e:
        print(json.dumps(dict(passed=False,error_type=type(e).__name__,error=str(e)),ensure_ascii=False),file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
