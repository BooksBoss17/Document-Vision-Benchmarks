"""Validate current derivation, actual PDF coordinate spaces, image pixels and XML positions."""
import sys
sys.dont_write_bytecode=True
import argparse,zipfile,posixpath,xml.etree.ElementTree as ET,re,io
from package_io import *
from recipe import *
def close_numbers(a,b):return len(a)==len(b) and all(type(x) in (int,float) and abs(x-y)<1e-4 for x,y in zip(a,b))
def xml_occurrences(source,source_uid):
    W='http://schemas.openxmlformats.org/wordprocessingml/2006/main';A='http://schemas.openxmlformats.org/drawingml/2006/main';V='urn:schemas-microsoft-com:vml';REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships';out=[]
    with zipfile.ZipFile(source) as z:
        for part in sorted(n for n in z.namelist() if re.fullmatch(r'word/(?:document|header\d+|footer\d+|footnotes|endnotes)\.xml',n)):
            relpart='word/_rels/'+Path(part).name+'.rels'
            if relpart not in z.namelist():continue
            raw=z.read(part);xml=ET.fromstring(raw);relations={x.attrib['Id']:posixpath.normpath(posixpath.join('word',x.attrib['Target'])) for x in ET.fromstring(z.read(relpart)) if x.attrib.get('TargetMode')!='External' and x.attrib.get('Type','').endswith('/image')};state={'p':-1,'image':0}
            def visit(el,pi=-1,branch=None):
                if el.tag=='{'+W+'}p':state['p']+=1;pi=state['p']
                local=el.tag.split('}')[-1]
                if local in {'Choice','Fallback'}:branch=local
                rid=el.attrib.get('{'+REL+'}embed') if el.tag=='{'+A+'}blip' else (el.attrib.get('{'+REL+'}id') if el.tag=='{'+V+'}imagedata' else None)
                if rid in relations:
                    source_hash=hashlib.sha256(raw).hexdigest();ordinal=state['image'];out.append({'source_uid':source_uid,'xml_occurrence_uid':'xml-occ-'+objsha([source_uid,part,source_hash,pi,ordinal,rid])[:24],'media_part':relations[rid],'relationship_id':rid,'paragraph_index_0':pi,'xml_image_order_0':ordinal,'xml_part':part,'alternate_content_branch':branch,'element_kind':local,'xml_source_sha256':source_hash,'rendered_occurrence_or_word_page_not_certified':True});state['image']+=1
                for child in el:visit(child,pi,branch)
            visit(xml)
    return out
def validate(root):
    root=root.resolve();count=verify_manifest(root);data=verify_exports(root)
    frozen=read(root/'review/baseline-files.json');actual={p.relative_to(root/'baseline').as_posix() for p in (root/'baseline').rglob('*') if p.is_file()};need(set(frozen)==actual,'BASELINE_INVENTORY_CHANGED')
    for rel,meta in frozen.items():need(sha(safe(root,'baseline/'+rel))==meta['sha256'],'BASELINE_FILE_CHANGED:'+rel)
    for rel,xs in {**data,'review/patches.jsonl':rows(root/'review/patches.jsonl'),'sources/pdf-geometry.jsonl':rows(root/'sources/pdf-geometry.jsonl'),'sources/source-inventory.jsonl':rows(root/'sources/source-inventory.jsonl')}.items():
        schema=read(root/'schemas'/(rel.replace('/','__')+'.schema.json'))
        for x in xs:schema_check(x,schema)
    from PIL import Image
    import fitz
    M=index(data['inputs/all-model-inputs.jsonl']);A=index(data['annotations/records.jsonl']);sources=index(data['sources/source-manifest.jsonl'],'source_uid');geometry=index(rows(root/'sources/pdf-geometry.jsonl'))
    need(len(M)==len(A)==200 and set(M)==set(A),'POPULATION_SCOPE')
    handles={};cache={};parsed_xml=[];pdfrows=0;source_inventory=index(rows(root/'sources/source-inventory.jsonl'),'source_uid')
    for sid,src in sources.items():
        p=safe(root,src['file']);need(sha(p)==src['sha256'],'SOURCE_HASH');need(sha(safe(root,src['document_inference_input']))==src['document_input_sha256'],'DOCUMENT_INPUT_HASH')
        handles[sid]=fitz.open(p) if src['kind']=='PDF' else zipfile.ZipFile(p)
        inv=source_inventory[sid]
        if src['kind']=='PDF':need(inv['total_original_pages']==len(handles[sid]),'SOURCE_PAGE_INVENTORY_DIFFERS')
        else:
            parsed_xml+=xml_occurrences(p,sid);bitmap=[];nonbitmap=[]
            for part in sorted(n for n in handles[sid].namelist() if n.startswith('word/media/')):
                try:
                    with Image.open(io.BytesIO(handles[sid].read(part))) as im:im.verify()
                    bitmap.append(part)
                except (OSError,ValueError):nonbitmap.append(part)
            need(inv['bitmap_media_parts']==bitmap and inv['non_bitmap_media_parts']==nonbitmap,'DOCX_MEDIA_INVENTORY_DIFFERS')
    recorded=rows(root/'baseline/sources/docx-xml-image-occurrences.jsonl');need(index(parsed_xml,'xml_occurrence_uid')==index(recorded,'xml_occurrence_uid'),'ACTUAL_XML_SOURCE_LOCATION_DIFFERS')
    try:
        for uid,m in M.items():
            need(set(m)==MODEL_KEYS,'MODEL_INPUT_FIELD_ALLOWLIST');need(m['input_fingerprint']==objsha({k:v for k,v in m.items() if k!='input_fingerprint'}),'INPUT_FINGERPRINT')
            need(sha(safe(root,m['image']))==m['image_sha256'] and sha(safe(root,m['original_crop']))==m['original_crop_sha256'],'IMAGE_HASH')
            need(A[uid]['input_fingerprint']==m['input_fingerprint'] and A[uid]['human_verified'] is False,'ANNOTATION_BINDING')
            with Image.open(safe(root,m['original_crop'])) as original:
                rgba=original.convert('RGBA');white=Image.new('RGBA',original.size,'white');white.alpha_composite(rgba);expected=white.convert('RGB').resize((m['width'],m['height']),Image.Resampling.NEAREST)
            with Image.open(safe(root,m['image'])) as visible:need(visible.size==(m['width'],m['height']) and visible.convert('RGB').tobytes()==expected.tobytes(),'VISIBLE_INPUT_PIXELS_CHANGED')
            loc=m['locator'];src=sources[m['source_uid']]
            if loc['kind']=='PDF_SOURCE_LINE':
                pdfrows+=1;g=geometry[uid];page=handles[m['source_uid']][loc['original_page_index_0']]
                need(src['original_page_indices_0'][loc['processed_page_index_0']]==loc['original_page_index_0'],'PDF_PAGE_MAPPING')
                need(loc['bbox_coordinate_space']=='ORIGINAL_PDF_PAGE_DISPLAY_POINTS' and loc['page_rotation']==page.rotation==g['page_rotation'],'PDF_COORDINATE_SPACE_OR_ROTATION')
                box=fitz.Rect(loc['bbox_pdf_pt']);unrot=box*page.derotation_matrix
                need(close_numbers(loc['bbox_pdf_unrotated_pt'],list(unrot)) and close_numbers(g['bbox_unrotated_pdf_pt'],list(unrot)) and close_numbers(list(unrot*page.rotation_matrix),list(box)),'PDF_COORDINATE_ROUNDTRIP')
                need(close_numbers(loc['page_rect_display_pt'],list(page.rect)) and close_numbers(loc['page_rect_unrotated_pt'],list(page.rect*page.derotation_matrix)),'PDF_PAGE_RECT')
                need(g['source_sha256']==src['sha256'] and g['original_page_index_0']==loc['original_page_index_0'],'GEOMETRY_SOURCE_BINDING')
                need(close_numbers(loc['bbox_source_pixels'],[v*2 for v in loc['bbox_pdf_pt']]),'PDF_PIXEL_POINT_UNITS')
                key=(m['source_uid'],loc['original_page_index_0'])
                if key not in cache:
                    pix=page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False);cache[key]=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
                frame=cache[key];x0,y0,x1,y1=loc['bbox_source_pixels'];need(0<=x0<x1<=frame.width and 0<=y0<y1<=frame.height,'PDF_BOUNDS')
                with Image.open(safe(root,loc['source_page_image'])) as stored:need(stored.convert('RGB').tobytes()==frame.tobytes(),'SOURCE_PAGE_PIXELS')
                with Image.open(safe(root,m['original_crop'])) as crop:need(crop.convert('RGB').tobytes()==frame.crop(tuple(int(x) for x in [x0,y0,x1,y1])).tobytes(),'CROP_NOT_DISPLAY_BBOX')
            else:
                need(loc['source_media_key']==m['source_uid']+':'+loc['media_part'],'DOCX_SOURCE_SCOPED_KEY')
                need(hashlib.sha256(handles[m['source_uid']].read(loc['media_part'])).hexdigest()==m['original_crop_sha256'],'DOCX_MEDIA_BYTES')
                expectedids=sorted(x['xml_occurrence_uid'] for x in parsed_xml if x['source_uid']==m['source_uid'] and x['media_part']==loc['media_part']);need(expectedids and sorted(loc['xml_occurrence_uids'])==expectedids,'DOCX_ACTUAL_XML_OCCURRENCES')
                need(loc['word_pagination_or_rendered_copy_count_certified'] is False,'UNSUPPORTED_WORD_PAGINATION_CLAIM')
            a=A[uid]
            need(type(a['critical']) is bool,'CRITICAL_TYPE')
            if a['annotation_status']==CONFIRMED:need(isinstance(a['reference_text'],str) and a['reference_text'].strip() and not a['uncertain_spans'],'UNRESOLVED_ACTIVE_REFERENCE')
            else:need(a['reference_text'] is None and 'CONFIRMED_TRANSCRIPTION_' not in a['content_type_basis'],'NONCONFIRMED_REFERENCE_OR_BASIS')
            proof=a['source_review'];need(sha(safe(root,proof['artifact']))==proof['artifact_sha256'],'REVIEW_ARTIFACT_BINDING')
        # All subset PDFs retain the source-page content/rotation mapping.
        for sid,src in sources.items():
            if src['kind']!='PDF':continue
            sub=fitz.open(safe(root,src['document_inference_input']))
            try:
                need(len(sub)==len(src['original_page_indices_0']),'PDF_SUBSET_PAGE_COUNT')
                # Match the actual 2px/pt source-raster protocol on BOTH sides.
                # Mixing 144dpi-warmed and cold 72dpi renders changes MuPDF image-cache sampling.
                for i,j in enumerate(src['original_page_indices_0']):need(sub[i].rotation==handles[sid][j].rotation and sub[i].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).samples==handles[sid][j].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).samples,'PDF_SUBSET_SOURCE_PAGE_DIFFERS')
            finally:sub.close()
    finally:
        for handle in handles.values():handle.close()
    return {'status':'PASS','version':VERSION,'counts':summary(data),'payload_files':count,'baseline_files':len(frozen),'pdf_regions_validated':pdfrows,'pdf_coordinate_roundtrips':pdfrows,'visible_images_replayed':len(M),'actual_xml_occurrences_validated':len(parsed_xml),'full_text_or_word_pagination_certified':False}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args()
    try:print(json.dumps(validate(a.root),ensure_ascii=False))
    except Exception as e:print(json.dumps({'status':'FAIL','error':str(e)},ensure_ascii=False));sys.exit(1)
