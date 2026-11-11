"""Stdlib-only package IO. Runtime bytecode is not payload; frozen historical files are."""
import sys
sys.dont_write_bytecode=True
import json,hashlib,math
from pathlib import Path
def need(ok,msg):
    if not ok:raise ValueError(msg)
def pairs(xs):
    d={}
    for k,v in xs:need(k not in d,'DUPLICATE_JSON_KEY:'+k);d[k]=v
    return d
def loads(s):return json.loads(s,object_pairs_hook=pairs,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('NONFINITE_JSON')))
def read(p):return loads(Path(p).read_text(encoding='utf8'))
def rows(p):return [loads(s) for s in Path(p).read_text(encoding='utf8').splitlines() if s.strip()]
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def objsha(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def write(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
def lines(p,xs):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(''.join(json.dumps(x,ensure_ascii=False,allow_nan=False)+'\n' for x in xs),encoding='utf8')
def index(xs,key='sample_uid'):
    d={}
    for x in xs:
        need(isinstance(x,dict) and isinstance(x.get(key),str) and x[key] and x[key] not in d,'INVALID_OR_DUPLICATE_ID:'+key);d[x[key]]=x
    return d
def safe(root,relative):
    need(isinstance(relative,str) and relative and ':' not in relative and '\\' not in relative and not relative.startswith('/') and '..' not in Path(relative).parts,'UNSAFE_PACKAGE_PATH')
    p=(root/relative).resolve();need(p.is_relative_to(root.resolve()) and p.is_file() and not (root/relative).is_symlink(),'MISSING_OR_EXTERNAL_FILE:'+relative);return p
def payload(relative):
    parts=Path(relative).parts
    return bool(parts) and (parts[0]=='baseline' or ('__pycache__' not in parts and Path(relative).suffix not in {'.pyc','.pyo'}))
def inventory(root):return {p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob('*')) if p.is_file() and p!=root/'checksums.sha256' and payload(p.relative_to(root))}
def seal_package(root):
    d=inventory(root);(root/'checksums.sha256').write_text(''.join(h+'  '+n+'\n' for n,h in d.items()),encoding='utf8');return d
def verify_manifest(root):
    expected={}
    for line in (root/'checksums.sha256').read_text(encoding='utf8').splitlines():
        h,n=line.split('  ',1);need(n not in expected and len(h)==64 and all(c in '0123456789abcdef' for c in h),'INVALID_CHECKSUM_ENTRY');need(n!='checksums.sha256' and payload(n),'INVALID_PAYLOAD_ENTRY');expected[n]=h
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p!=root/'checksums.sha256' and payload(p.relative_to(root))}
    need(set(expected)==actual,'PACKAGE_FILE_INVENTORY_MISMATCH')
    for n,h in expected.items():need(sha(safe(root,n))==h,'PACKAGE_FILE_HASH_MISMATCH:'+n)
    return len(expected)
def schema_check(v,s):
    t={dict:'object',list:'array',str:'string',int:'integer',float:'number',bool:'boolean',type(None):'null'}[type(v)];expected=s.get('type',[t]);expected=[expected] if isinstance(expected,str) else expected;need(t in expected or (t=='integer' and 'number' in expected),'SCHEMA_TYPE')
    if 'enum' in s:need(v in s['enum'],'SCHEMA_ENUM')
    if isinstance(v,dict):
        need(set(s.get('required',[]))<=set(v),'SCHEMA_MISSING_FIELD')
        if s.get('additionalProperties') is False:need(set(v)<=set(s.get('properties',{})),'SCHEMA_EXTRA_FIELD')
        for k,x in v.items():
            if k in s.get('properties',{}):schema_check(x,s['properties'][k])
    if isinstance(v,list):
        for x in v:schema_check(x,s.get('items',{}))
