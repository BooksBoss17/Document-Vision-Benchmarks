"""SHA-bound local dependency access for separately authorized benchmark data.

An index contains relative object paths and opaque historical path selectors.
Selectors never become filesystem paths. All actual reads stay in the supplied
bundle. This module does not download data, widen a license or grant admission.
"""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
import hashlib,json,re,stat

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def selector(value):
    value=str(value).replace('\\','/')
    match=re.search(r'(?:^|/)([A-Za-z]:/.*)$',value)
    return (match.group(1) if match else value).casefold()

def relative_file(root,value):
    if not isinstance(value,str) or not value or '\\' in value:
        raise ValueError('Dependency object must be a nonempty POSIX relative path')
    posix=PurePosixPath(value);windows=PureWindowsPath(value)
    if posix.is_absolute() or windows.drive or ':' in value or '..' in posix.parts:
        raise ValueError('Unsafe dependency object path')
    current=root
    for part in posix.parts:
        current=current/part
        attrs=getattr(current.lstat(),'st_file_attributes',0)
        if current.is_symlink() or attrs & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
            raise ValueError('Dependency object traverses a link')
    actual=current.resolve()
    if not actual.is_relative_to(root) or not actual.is_file():
        raise ValueError('Dependency object missing or outside its bundle')
    return actual

def unique_pairs(items):
    result={}
    for key,value in items:
        if key in result:raise ValueError('Duplicate dependency index key')
        result[key]=value
    return result

class PortableAccess:
    def __init__(self,root,index):
        self.root=Path(root).resolve();self.index=Path(index).resolve()
        self.workspace=self.index.parent
        data=json.loads(self.index.read_text('utf-8-sig'),object_pairs_hook=unique_pairs)
        if data.get('schema')!='ekb-licensed-dependency-index-v1' or not isinstance(data.get('files'),list):
            raise ValueError('Unsupported licensed dependency index')
        self.by_hash={};self.by_alias={};self.remapped={};self.used={}
        self.runtime_sources={str(Path(__file__).resolve()):sha(__file__)}
        self.frozen={str(self.index):sha(self.index),**self.runtime_sources}
        for row in data['files']:
            digest=row.get('sha256');path=row.get('path');aliases=row.get('aliases',[])
            if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
                raise ValueError('Full lower-case dependency SHA256 required')
            if not isinstance(aliases,list) or any(not isinstance(x,str) or not x for x in aliases):
                raise ValueError('Invalid dependency selectors')
            target=relative_file(self.workspace,path)
            if digest in self.by_hash and self.by_hash[digest]!=target:
                raise ValueError('Ambiguous dependency object SHA256')
            self.by_hash[digest]=target
            for alias in aliases:
                key=selector(alias)
                if key in self.by_alias and self.by_alias[key]!=(target,digest):
                    raise ValueError('Ambiguous historical dependency selector')
                self.by_alias[key]=(target,digest)
        # These are original proof bytes already shipped in the supplied input
        # package. The inherited caller still checks its expected source digest.
        self.audit_copies={}
        manifest=self.root/'checksums.sha256'
        if manifest.is_file():
            self.frozen[str(manifest)]=sha(manifest)
            for line in manifest.read_text('utf8').splitlines():
                digest,rel=line.split('  ',1)
                if rel.startswith(('evidence/','sources/','review/')) and Path(rel).suffix in {'.json','.jsonl'}:
                    if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid package proof digest')
                    self.audit_copies.setdefault(digest,relative_file(self.root,rel))

    @lru_cache(maxsize=8192)
    def locate(self,path,digest=None):
        if digest is not None:
            if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid requested dependency SHA256')
            target=self.audit_copies.get(digest,self.by_hash.get(digest))
            expected=digest
        else:
            record=self.by_alias.get(selector(path))
            target,expected=record if record is not None else (None,None)
        if target is None:raise FileNotFoundError('Required licensed dependency is absent from the local index: '+str(path))
        base=self.root if target.is_relative_to(self.root) else self.workspace
        checked=relative_file(base,target.relative_to(base).as_posix())
        if sha(checked)!=expected:raise ValueError('Licensed dependency SHA256 differs: '+str(path))
        self.used[str(checked)]=expected;self.remapped[str(path)]=str(checked)
        return checked

    def freeze_runtime_access(self,path):
        path=Path(path).resolve()
        if path.parent!=Path(__file__).resolve().parent or path.name!='runtime_access.py':
            raise ValueError('Unexpected portable access runtime module')
        self.runtime_sources[str(path)]=sha(path)
        self.frozen[str(path)]=self.runtime_sources[str(path)]

    def verify(self):
        for name,expected in {**self.frozen,**self.used,**self.runtime_sources}.items():
            p=Path(name)
            base=self.root if p.is_relative_to(self.root) else self.workspace
            # The adapter itself may live outside both data roots; its pinned
            # bytes are checked as executable code, never selected by the index.
            if name not in self.runtime_sources:relative_file(base,p.relative_to(base).as_posix())
            if sha(p)!=expected:raise ValueError('Portable runtime/dependency changed during this run: '+name)
