"""Same-canvas source-figure ablation, without changing target pixels. CPU only."""
import hashlib
import json
import math
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw

DESIGN = 'SAME_CANVAS_FIGURE_ABLATION'
VARIANTS = {'figure_removed', 'figure_present'}
ALGORITHM = 'WHITE_FIGURES_EXCEPT_TARGETS_RGB_V1'
MAX_PIXELS = 100_000_000


def pixel_box(box, width, height):
    if not isinstance(box, list) or len(box) != 4 or not all(type(x) in {int,float} and math.isfinite(x) for x in box):
        raise ValueError('invalid intervention box')
    x0,y0,x1,y1 = box
    if not (x0 < x1 and y0 < y1):
        raise ValueError('empty intervention box')
    b = [max(0,math.floor(x0)), max(0,math.floor(y0)), min(width,math.ceil(x1)), min(height,math.ceil(y1))]
    if b[0] >= b[2] or b[1] >= b[3]:
        raise ValueError('intervention box outside source')
    return b


def make_recipe(width, height, figures, targets):
    if width * height > MAX_PIXELS:
        raise ValueError('controlled pair source exceeds bounded image pixel budget')
    boxes = lambda rows: sorted({tuple(pixel_box(r['bbox'],width,height)) for r in rows})
    return dict(version=1, algorithm=ALGORITHM, fill=[255,255,255], source_size=[width,height],
                figure_boxes=[list(b) for b in boxes(figures)], protected_boxes=[list(b) for b in boxes(targets)])


def recipe_key(source_sha256, box, view_kind, recipe=None):
    value = dict(source_sha256=source_sha256, source_crop_box=box, view_kind=view_kind, mask_recipe=recipe)
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def render(source_path, destination, view_kind, box, recipe=None):
    """One decoded source/view at a time; never resize or mask outside figures."""
    with Image.open(source_path) as source:
        if source.width * source.height > MAX_PIXELS:
            raise ValueError('controlled pair source exceeds bounded image pixel budget')
        image = source.convert('RGB')
        try:
            if view_kind == 'figure_removed':
                if not recipe or recipe['algorithm'] != ALGORITHM:
                    raise ValueError('missing/unknown mask recipe')
                draw = ImageDraw.Draw(image)
                for x0,y0,x1,y1 in recipe['figure_boxes']:
                    draw.rectangle((x0,y0,x1-1,y1-1),fill=(255,255,255))
                for target in recipe['protected_boxes']:
                    patch = source.crop(target).convert('RGB')
                    try:
                        image.paste(patch,(target[0],target[1]))
                    finally:
                        patch.close()
            crop = image.crop(box)
            try:
                crop.save(destination)
            finally:
                crop.close()
        finally:
            image.close()


def qualification(source_path, removed_path, recipe):
    overlap = any(min(f[2],t[2])>max(f[0],t[0]) and min(f[3],t[3])>max(f[1],t[1])
                  for f in recipe['figure_boxes'] for t in recipe['protected_boxes'])
    changed, extent = 0, None
    with Image.open(source_path) as source, Image.open(removed_path) as removed:
        for top in range(0,source.height,64):
            box = (0,top,source.width,min(source.height,top+64))
            with source.crop(box).convert('RGB') as a, removed.crop(box).convert('RGB') as b:
                with ImageChops.difference(a,b) as diff:
                    # Count changed pixels, not changed RGB channels.
                    channels = diff.split()
                    try:
                        with ImageChops.lighter(channels[0],channels[1]) as rg:
                            with ImageChops.lighter(rg,channels[2]) as mask:
                                hist=mask.histogram(); changed += sum(hist[1:])
                                bb=mask.getbbox()
                    finally:
                        for channel in channels:channel.close()
                    if bb:
                        bb=[bb[0],bb[1]+top,bb[2],bb[3]+top]
                        extent=bb if extent is None else [min(extent[0],bb[0]),min(extent[1],bb[1]),max(extent[2],bb[2]),max(extent[3],bb[3])]
    reasons=[]
    if overlap:reasons.append('FIGURE_TARGET_OVERLAP')
    if not changed:reasons.append('NO_EFFECTIVE_FIGURE_INTERVENTION')
    return dict(qualified=not reasons,reasons=reasons,changed_pixel_count=changed,changed_bbox=extent,figure_target_overlap=overlap)
