"""Local archive-aware access with frozen runtime policy and legacy isolation."""
from pathlib import Path
import hashlib,sys,json,inspect,importlib,os
from functools import lru_cache

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()

def workspace(root):
    for p in [Path(root).resolve(),*Path(root).resolve().parents]:
        if (p/'workspace.json').is_file() and (p/'scripts/workspace/paths.py').is_file():return p
    raise ValueError('v2.4 local package requires its registered developer workspace')

class Access:
    def __init__(self,root):
        portable=os.environ.get('EKB_BENCH_DEPENDENCIES')
        if portable:
            from portable_access import PortableAccess
            self._portable=PortableAccess(root,portable)
            for name in ('root','workspace','frozen','remapped','audit_copies'):
                setattr(self,name,getattr(self._portable,name))
            self._portable.freeze_runtime_access(__file__)
            return
        self.root=Path(root).resolve();self.workspace=workspace(root)
        sys.path.insert(0,str(self.workspace/'scripts/workspace'))
        from paths import resolve_path,load_mappings
        self.resolve_path=resolve_path;self.settings=json.loads((self.workspace/'workspace.json').read_text(encoding='utf-8'))
        self.pathmap=self.workspace/'docs/workspace/path-map.jsonl';self.archivemap=self.workspace/'docs/workspace/archive-map.jsonl'
        self.mappings=load_mappings(self.pathmap);self.archives=load_mappings(self.archivemap)
        self.frozen={str(p):sha(p) for p in [Path(__file__),self.workspace/'workspace.json',self.pathmap,self.archivemap,self.workspace/'scripts/workspace/paths.py']}
        self.remapped={}
        self.audit_copies={sha(p):p for p in (self.root/'sources/audits').glob('*.jsonl')}
        # Content-addressed reuse of existing proof bytes, not new provenance.
        # Every legacy _bound caller independently checks the requested SHA.
        for line in (self.root/'checksums.sha256').read_text(encoding='utf-8').splitlines():
            digest,rel=line.split('  ',1)
            member=Path(rel)
            if member.is_absolute() or '..' in member.parts:raise ValueError('unsafe proof index member')
            if rel.startswith(('evidence/','sources/','review/')) and member.suffix in ('.json','.jsonl'):
                self.audit_copies.setdefault(digest,self.root/member)

    @lru_cache(maxsize=8192)
    def locate(self,path,digest=None):
        if hasattr(self,'_portable'):return self._portable.locate(path,digest)
        if digest in self.audit_copies:
            target=self.audit_copies[digest]
        else:
            target=self.resolve_path(str(path),root=self.workspace,mappings=self.mappings,archive_mappings=self.archives,
                archive_roots=self.settings['archive_roots'],archive_source_roots=self.settings.get('archive_source_roots',[]))
        if not target.is_file():raise FileNotFoundError('Required frozen dependency unavailable: '+str(path)+' -> '+str(target))
        if str(target)!=str(path):self.remapped[str(path)]=str(target)
        return target

    def verify(self):
        if hasattr(self,'_portable'):return self._portable.verify()
        for p,d in self.frozen.items():
            if sha(p)!=d:raise ValueError('runtime/map changed during run: '+p)

def install_legacy(access):
    source=importlib.import_module('legacy_v23.source_admission_v21')
    resolution=importlib.import_module('legacy_v23.resolution_admission')
    visual=importlib.import_module('legacy_v23.final_visual_admission')
    original=source._bound
    contract=json.loads((access.root/'sources/v23-parent-contract.json').read_text(encoding='utf-8'))
    root_hashes={'checksums.sha256':contract['checksums_sha256'],**contract['files']}
    expected_root=(access.workspace/contract['relative_workspace_root']).resolve()
    def bound(path,digest,cache):return original(access.locate(str(path),digest),digest,cache)
    for module in (source,resolution,visual):module._bound=bound
    def root_file(root,relative,cache):
        if Path(root).resolve()!=expected_root or relative not in root_hashes:raise ValueError('unregistered ancestor root/member')
        return bound(Path(root)/relative,root_hashes[relative],cache)
    resolution._root_file=root_file
    geom=importlib.import_module('legacy_v23.validate')
    text=inspect.getsource(geom.controlled_geometry)
    seams={"source_path=Path(binding['image_path'])":"source_path=_locate_source(binding['image_path'])",
           "shard=Path(proof['shard_path']).resolve()":"shard=_locate_source(proof['shard_path']).resolve()"}
    for before,after in seams.items():
        if text.count(before)!=1:raise ValueError('legacy geometry source seam changed')
        text=text.replace(before,after)
    geom._locate_source=access.locate
    exec(compile(text,str(Path(geom.__file__))+' [v24 dependency access]','exec'),geom.__dict__)
    return geom
