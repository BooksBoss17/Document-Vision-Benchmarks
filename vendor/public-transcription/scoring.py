"""V2 input-bound transcription scorer. Standard library only; never runs a model.

Run with --help. score_run() creates a new immutable evidence directory and
returns the report. No heterogeneous overall accuracy or LaTeX semantic claim.
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode=True

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import sys
import unicodedata
from pair_reporting import dependence_summary
from package_integrity import verify_package, selection_scope
from comparison_limits import reset_work, reserve_work, validate_text, MAX_PREDICTION_VIEW_CHARACTERS, MAX_PREDICTION_FILE_BYTES
from curation_admission import state as admission_state

if __package__:
    from .latex_layout import compare_latex_layout
    from .source_admission import validate_source_proof
    from .final_visual_admission import validate_visual
    from .table_policy import normalize_table_layout, POLICY as TABLE_POLICY
else:
    from latex_layout import compare_latex_layout
    from source_admission import validate_source_proof
    from final_visual_admission import validate_visual
    from table_policy import normalize_table_layout, POLICY as TABLE_POLICY

REVIEWED = {"REVIEWED", "CORRECTED_REVIEWED"}
ADMITTED = REVIEWED | {"SOURCE_VERIFIED"}
DATASET_VERSION = "2.4.0"
MIXED_PROFILE = "UPSTREAM_TEX_MIXED_V1"
PAGE_SCOPES = {"ANNOTATED_REGIONS", "FULL_VISIBLE_CONTENT"}
FORMATS = {"TEXT", "LATEX", "TABLE_HTML"}
HEX = re.compile(r"[0-9a-fA-F]{64}\Z")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def decode_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"invalid JSON constant: {x}")))


def jsonl(data):
    return [decode_json(line.encode("utf-8")) for line in data.decode("utf-8-sig").splitlines() if line.strip()]


def uid_map(rows, label):
    result = {}
    for row in rows:
        require(isinstance(row, dict), f"{label}: row must be object")
        uid = row.get("sample_uid")
        require(isinstance(uid, str) and bool(uid.strip()), f"{label}: invalid sample_uid")
        require(uid not in result, f"{label}: duplicate sample_uid {uid}")
        result[uid] = row
    return result


def normalize_text(text):
    """NFC and whitespace collapse; preserves punctuation, case and word spaces."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def mixed_tokens(text):
    """NFC lexical mixed tokens; retain controls, scripts, symbols and word gaps.

    Collapse whitespace to one SPACE token, exactly as ordinary TEXT collapses
    whitespace. Never strip TeX controls or rewrite upstream serialization into
    prose, and never infer algebraic equivalence or missing low-resolution text.
    """
    return re.findall(r"\\(?:[A-Za-z]+|[^\r\n])|\s+|.",
                      unicodedata.normalize("NFC", text), re.DOTALL)


def mixed_metrics(reference, predicted):
    a = ["SPACE" if token.isspace() else token for token in mixed_tokens(reference)]
    b = ["SPACE" if token.isspace() else token for token in mixed_tokens(predicted)]
    # Leading/trailing whitespace has no content under the TEXT contract.
    a = a[1:] if a[:1] == ["SPACE"] else a
    a = a[:-1] if a[-1:] == ["SPACE"] else a
    b = b[1:] if b[:1] == ["SPACE"] else b
    b = b[:-1] if b[-1:] == ["SPACE"] else b
    errors = edit_distance(a, b)
    return dict(exact=a == b, errors=errors, reference_units=len(a),
                cer=errors / max(1, len(a)), token_errors=errors,
                token_error_rate=errors / max(1, len(a)),
                unit="MIXED_SERIALIZATION_TOKENS", serialization_profile=MIXED_PROFILE)


def resolve_selection(root, selection):
    """Use the answer-free admitted inventory by default; retain the old alias."""
    if selection in {"admitted-all", "reviewed-all"}:
        return root / "selections/admitted-all.json"
    if selection is None:
        candidate = root / "selections/admitted-all.json"
        return candidate if candidate.exists() else None
    return Path(selection)


def edit_distance(a,b):
    if a==b:return 0
    # Trim identical context before reserving quadratic work. This preserves
    # exact edit distance and permits a one-character error in a long passage.
    left=0
    while left<min(len(a),len(b)) and a[left]==b[left]:left+=1
    a,b=a[left:],b[left:]
    right=0
    while right<min(len(a),len(b)) and a[-1-right]==b[-1-right]:right+=1
    if right:a,b=a[:-right],b[:-right]
    if not a:return len(b)
    if not b:return len(a)
    reserve_work(len(a)*len(b))
    if len(a)<len(b):a,b=b,a
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current=[i]
        for j,y in enumerate(b,1):current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
        previous=current
    return previous[-1]


def text_metrics(reference, predicted):
    a, b = normalize_text(reference), normalize_text(predicted)
    errors = edit_distance(a, b)
    return dict(exact=a == b, errors=errors, reference_units=len(a), cer=errors / max(1, len(a)))


def latex_tokens(text):
    """Lexical math tokens. A control word consumes *all* following ASCII letters.

    Whitespace separates control words but otherwise is ignored in math; spaces
    within \\text groups are retained. No macro expansion or semantic inference.
    """
    text = unicodedata.normalize("NFC", text)
    tokens, stack, i, pending_text = [], [], 0, False
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            if any(stack) and (not tokens or tokens[-1] != "SPACE"):
                tokens.append("SPACE")
            i += 1
            continue
        if ch == "\\":
            match = re.match(r"\\(?:[A-Za-z]+|[^\r\n])", text[i:])
            if not match:
                tokens.append("INVALID_BACKSLASH")
                i += 1
                continue
            token = match.group()
            tokens.append(token)
            pending_text = token in {r"\text", r"\textrm", r"\textbf", r"\textit", r"\operatorname", r"\mbox"}
            i += len(token)
            continue
        tokens.append(ch)
        if ch == "{":
            stack.append(pending_text or (bool(stack) and stack[-1]))
        elif ch == "}" and stack:
            stack.pop()
        pending_text = False
        i += 1
    return tokens


def latex_signature(tokens):
    return [t if t.startswith("\\") or t in {"{", "}", "^", "_", "&", "SPACE", "INVALID_BACKSLASH"}
            else "ATOM" for t in tokens]


def latex_balanced(tokens):
    depth = 0
    for token in tokens:
        if token == "{":
            depth += 1
        if token == "}":
            depth -= 1
        if depth < 0:
            return False
    return depth == 0 and "INVALID_BACKSLASH" not in tokens


def latex_metrics(reference, predicted):
    a, b = latex_tokens(reference), latex_tokens(predicted)
    errors = edit_distance(a, b)
    valid = latex_balanced(b)
    layout = compare_latex_layout(reference, predicted)
    coverage = {key: value for key, value in layout.items()
                if key not in {'normalized_expression_exact', 'layout_structure_exact'}}
    # Keep the original lexical exact/error contract. The new normalized/layout
    # metrics are separately named and carry explicit reference-only coverage.
    return dict(exact=a == b and valid, token_exact=a == b and valid,
                errors=errors, token_errors=errors, reference_units=len(a),
                token_error_rate=errors / max(1, len(a)),
                structural_token_exact=valid and latex_signature(a) == latex_signature(b),
                balanced_prediction=valid,
                normalized_expression_exact=layout['normalized_expression_exact'],
                layout_structure_exact=layout['layout_structure_exact'], layout_coverage=coverage)


class TableParser(HTMLParser):
    """Strict table subset, real DOM nesting and an expanded span occupancy grid."""
    GROUPS = {"thead", "tbody", "tfoot"}
    INLINE = {"span", "b", "i", "u", "em", "strong", "sup", "sub", "p", "div", "ul", "ol", "li"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.rows, self.cell = [], [], None
        self.tables = 0

    def handle_starttag(self, tag, attrs):
        parent = self.stack[-1] if self.stack else None
        if tag == "table":
            require(not self.stack and self.tables == 0, "nested/multiple table")
            self.tables += 1
        elif tag in self.GROUPS:
            require(parent == "table", "table group nesting")
        elif tag == "tr":
            require(parent in self.GROUPS | {"table"}, "row outside table/group")
            self.rows.append([])
        elif tag in {"td", "th"}:
            require(parent == "tr" and self.cell is None, "cell outside row/unclosed cell")
            attrs = dict(attrs)
            spans = []
            for key in ("rowspan", "colspan"):
                value = attrs.get(key, "1")
                require(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value), "invalid table span")
                require(int(value) <= 10000, "excessive table span")
                spans.append(int(value))
            self.cell = {"tag": tag, "rowspan": spans[0], "colspan": spans[1], "parts": []}
            self.rows[-1].append(self.cell)
        elif tag == "br":
            require(self.cell is not None, "br outside cell")
            self.cell["parts"].append("\n")
            return
        elif tag in self.INLINE:
            require(self.cell is not None, "inline element outside cell")
            if tag == "li":
                require(parent in {"ul", "ol"}, "li outside list")
            if tag in {"p", "div", "ul", "ol", "li"}:
                self.cell["parts"].append("\n")
        else:
            raise ValueError(f"unsupported table element: {tag}")
        require(len(self.stack)<64, "SCORING_CAPACITY_EXCEEDED: HTML nesting")
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        require(tag == "br", "only br may self-close")
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        require(self.stack and self.stack[-1] == tag, "malformed table closing order")
        self.stack.pop()
        if tag in {"td", "th"}:
            self.cell = None
        elif tag in {"p", "div", "ul", "ol", "li"} and self.cell:
            self.cell["parts"].append("\n")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["parts"].append(data)
        else:
            require(not data.strip(), "text outside cells")

    def handle_decl(self, decl):
        raise ValueError("doctype not permitted in table fragment")

    def result(self):
        require(self.tables == 1 and not self.stack and self.rows, "unclosed/empty table")
        grid, cells = {}, []
        for row_idx, row in enumerate(self.rows):
            col = 0
            for raw in row:
                while (row_idx, col) in grid:
                    col += 1
                rs, cs = raw["rowspan"], raw["colspan"]
                require(len(grid) + rs * cs <= 100000, "table grid exceeds bounded evaluator capacity")
                require(row_idx + rs <= len(self.rows), "rowspan extends outside table")
                for r in range(row_idx, row_idx + rs):
                    for c in range(col, col + cs):
                        require((r, c) not in grid, "overlapping cells")
                        grid[r, c] = len(cells)
                cells.append(dict(row=row_idx, col=col, tag=raw["tag"], rowspan=rs, colspan=cs,
                                  text=normalize_text("".join(raw["parts"]))))
                col += cs
        width = max((c for _, c in grid), default=-1) + 1
        require(width and len(grid) == len(self.rows) * width, "ragged/incomplete table grid")
        return dict(rows=len(self.rows), columns=width, cells=cells)


def parse_table(text):
    require(isinstance(text,str) and len(text)<=131072, "SCORING_CAPACITY_EXCEEDED: table length")
    parser = TableParser()
    parser.feed(text)
    parser.close()
    return parser.result()


def table_metrics(reference, predicted):
    validate_text(predicted)
    a = parse_table(reference)
    try:
        b = parse_table(predicted)
        valid = True
    except ValueError as exc:
        if "SCORING_CAPACITY_EXCEEDED" in str(exc):raise
        b, valid = dict(rows=0, columns=0, cells=[]), False
    structure = lambda t: (t["rows"], t["columns"], [{k: v for k, v in c.items() if k != "text"} for c in t["cells"]])
    def slots(cell):
        return {(r,c) for r in range(cell['row'],cell['row']+cell['rowspan'])
                for c in range(cell['col'],cell['col']+cell['colspan'])}
    def empty_slots(table):
        return set().union(*(slots(c) for c in table['cells'] if not c['text']))
    # No visible content establishes a blank-cell border. Only a cell whose
    # ENTIRE coverage is empty on BOTH sides may lose its span. Nonempty spans
    # and blank spans touching a nonempty counterpart remain fully strict.
    common_empty = empty_slots(a) & empty_slots(b)
    def unitize(table):
        cells=[]
        for cell in table['cells']:
            covered=slots(cell)
            if not cell['text'] and covered <= common_empty:
                cells.extend(dict(cell,row=r,col=c,rowspan=1,colspan=1) for r,c in sorted(covered))
            else:
                cells.append(cell)
        return dict(table,cells=sorted(cells,key=lambda c:(c['row'],c['col'])))
    raw_a,raw_b=a,b
    a,b=unitize(a),unitize(b)
    ac = {(c["row"], c["col"]): c for c in a["cells"]}
    bc = {(c["row"], c["col"]): c for c in b["cells"]}
    errors = sum(edit_distance(ac.get(k, {}).get("text", ""), bc.get(k, {}).get("text", "")) for k in ac.keys() | bc.keys())
    units = sum(len(c["text"]) for c in a["cells"])
    # Keep the original gold cell denominator; normalization adds comparison
    # units, not extra gold cells. Every original reference cell counts once.
    exact_cells=0
    for cell in raw_a['cells']:
        covered=slots(cell);key=(cell['row'],cell['col'])
        if not cell['text'] and covered <= common_empty:
            exact_cells+=all(k in bc and not bc[k]['text'] and bc[k]['tag']==cell['tag']
                             and bc[k]['rowspan']==bc[k]['colspan']==1 for k in covered)
        else:
            exact_cells+=key in bc and cell['text']==bc[key]['text']
    structural = valid and structure(a) == structure(b)
    return dict(exact=structural and exact_cells == len(raw_a['cells']), structure_exact=structural,
                raw_structure_exact=valid and structure(raw_a)==structure(raw_b),
                jointly_empty_grid_slots=len(common_empty),
                comparison_reference_cells=len(ac),comparison_prediction_cells=len(bc),
                valid_prediction=valid, cell_text_errors=errors, reference_cell_characters=units,
                aligned_cell_text_cer=errors / max(1, units), reference_cells=len(raw_a['cells']),
                exact_aligned_cells=exact_cells, extra_cells=len(bc.keys() - ac.keys()),
                missing_cells=len(ac.keys() - bc.keys()))


def content_metrics(fmt, reference, predicted, serialization_profile=None, table_policy=None):
    if table_policy is not None:
        require(fmt=="TABLE_HTML" and table_policy==TABLE_POLICY,"invalid table serialization policy")
        reference,predicted=map(normalize_table_layout,(reference,predicted))
    if serialization_profile == MIXED_PROFILE:
        require(fmt == "TEXT", "mixed serialization profile requires TEXT")
        return mixed_metrics(reference, predicted)
    require(fmt in FORMATS, "UNSUPPORTED_TASK format " + str(fmt))
    return {"TEXT": text_metrics, "LATEX": latex_metrics, "TABLE_HTML": table_metrics}[fmt](reference, predicted)


def page_metrics(gold, prediction, task=None):
    task = task or {}
    expected = gold["blocks"]
    actual = prediction.get("blocks", []) if prediction else []
    ga, pa = {b["region_id"]: b for b in expected}, {b["region_id"]: b for b in actual}
    content = []
    for block in expected:
        rid = block["region_id"]
        metric = content_metrics(block["reference_format"], block["text"], pa.get(rid, {}).get("text", ""),
                                 task.get("serialization_profile") if block["reference_format"] == "TEXT" else None)
        if rid not in pa:
            metric["exact"] = False
        content.append(dict(region_id=rid, reference_format=block["reference_format"], missing=rid not in pa, **metric))
    exempt = set(gold.get("unordered_region_ids", []))
    order_a = [rid for rid in ga if rid not in exempt]
    order_b = [rid for rid in pa if rid not in exempt]
    missing, extra = len(ga.keys() - pa.keys()), len(pa.keys() - ga.keys())
    shared = [rid for rid in order_b if rid in order_a]
    rank = {rid: n for n, rid in enumerate(order_a)}
    inversions = sum(rank[shared[i]] > rank[shared[j]] for i in range(len(shared)) for j in range(i + 1, len(shared)))
    possible = len(order_a) * (len(order_a) - 1) // 2
    correct_pairs = sum(order_a[i] in pa and order_a[j] in pa and order_b.index(order_a[i]) < order_b.index(order_a[j])
                        for i in range(len(order_a)) for j in range(i + 1, len(order_a)))
    all_content = not missing and not extra and all(b["exact"] for b in content)
    applicable = len(order_a) >= 2
    order_exact = order_a == order_b if applicable else None
    return dict(exact=all_content and order_exact is not False, content_exact=all_content,
                task_scope=task.get("scope", "LEGACY_EXPLICIT_REGIONS"),
                full_visible_content_verified=False,
                order_exact=order_exact, order_status="APPLICABLE" if applicable else "NOT_APPLICABLE",
                order_scope="NON_EXEMPT_MAIN_FLOW_BLOCKS" if exempt else "ALL_REFERENCE_BLOCKS",
                ordered_reference_blocks=len(order_a), unordered_reference_blocks=len(exempt),
                unordered_region_ids=sorted(exempt),
                reference_blocks=len(ga), missing_blocks=missing,
                extra_blocks=extra, exact_blocks=sum(b["exact"] for b in content),
                common_block_inversions=inversions, reference_order_pairs=possible,
                correct_order_pairs=correct_pairs, order_pair_recall=correct_pairs / possible if possible else None,
                blocks=content)


def validate_inputs(inputs, root):
    images, pairs = [], defaultdict(list)
    for uid, row in inputs.items():
        require(row.get("protocol") in {"REGION_TRANSCRIPTION", "PAGE_TRANSCRIPTION", "PAIRED_INTERFERENCE"}, f"invalid protocol {uid}")
        task = row.get("task")
        require(isinstance(task, dict), f"invalid task {uid}")
        require(isinstance(task.get("instruction"), str) and task["instruction"].strip(), f"invalid task instruction {uid}")
        width, height = row.get("width"), row.get("height")
        require(type(width) is int and width > 0 and type(height) is int and height > 0, f"invalid image dimensions {uid}")
        regions = task.get("target_regions")
        require(isinstance(regions, list), f"invalid target_regions {uid}")
        region_ids = set()
        for region in regions:
            require(isinstance(region, dict), f"invalid target region {uid}")
            rid, bbox = region.get("region_id"), region.get("bbox")
            require(isinstance(rid, str) and rid.strip() and rid not in region_ids, f"invalid/duplicate target region ID {uid}")
            require(isinstance(bbox, list) and len(bbox) == 4 and all(type(v) in {int, float} and math.isfinite(v) for v in bbox), f"invalid region bbox {uid}")
            require(0 <= bbox[0] < bbox[2] <= width and 0 <= bbox[1] < bbox[3] <= height, f"out of bounds region {uid}")
            region_ids.add(rid)
        validate_task_profile(task, row["protocol"], uid)
        fmt = task.get("output_format")
        require(fmt in (FORMATS | {"PAGE_BLOCKS"}), f"invalid output_format {uid}")
        require((fmt == "PAGE_BLOCKS") == (row["protocol"] == "PAGE_TRANSCRIPTION"), f"page format mismatch {uid}")
        if row["protocol"] == "PAGE_TRANSCRIPTION":
            validate_page_scope(task, region_ids, uid)
        else:
            require(not ({"region_formats", "unordered_region_ids"} & task.keys()), f"PAGE-only scope fields {uid}")
        if row["protocol"] == "PAIRED_INTERFERENCE":
            require(isinstance(row.get("pair_id"), str) and row["pair_id"], f"pair ID missing {uid}")
            require(row.get("pair_variant") in {"crop", "context", "figure_removed", "figure_present"}, f"pair variant invalid {uid}")
            pairs[row["pair_id"]].append(row["pair_variant"])
        require(isinstance(row.get("image"), str) and row["image"], f"invalid image {uid}")
        path = (root / row["image"]).resolve()
        require(not Path(row["image"]).is_absolute() and ".." not in Path(row["image"]).parts and path.is_relative_to(root), f"image escapes package {uid}")
        require(path.is_file(), f"missing image {uid}")
        require(isinstance(row.get("image_sha256"), str) and HEX.fullmatch(row["image_sha256"]), f"invalid image hash {uid}")
        actual = sha256(path.read_bytes())
        require(actual == row["image_sha256"].lower(), f"image hash drift {uid}")
        images.append(dict(sample_uid=uid, path=str(path), sha256=actual))
    for pid, variants in pairs.items():
        require(len(variants) == 2 and set(variants) in ({"crop", "context"},{"figure_removed", "figure_present"}), f"pair must have exactly two distinct input members {pid}")
        if set(variants)=={"figure_removed","figure_present"}:
            members=[r for r in inputs.values() if r.get('pair_id')==pid]
            require(all(r.get('metadata',{}).get('pair_design')=='SAME_CANVAS_FIGURE_ABLATION' for r in members),f'controlled pair design missing {pid}')
            require(members[0]['task']==members[1]['task'],f'controlled pair literal tasks differ {pid}')
            require((members[0]['width'],members[0]['height'])==(members[1]['width'],members[1]['height']),f'controlled pair canvas differs {pid}')
    return images


def validate_task_profile(task, protocol, uid):
    policy=task.get("table_serialization_policy")
    require(policy is None or (policy==TABLE_POLICY and task.get("output_format")=="TABLE_HTML" and policy in task.get("instruction","")),f"unknown/incompatible table policy {uid}")
    annotation=task.get("annotation_policy")
    require(annotation is None or annotation=="CASIA_NATIVE_LABELLED_TEXT_V1",f"unknown annotation policy {uid}")
    profile = task.get("serialization_profile")
    require(profile is None or (profile == MIXED_PROFILE and (task.get("output_format") == "TEXT" or
            (protocol == "PAGE_TRANSCRIPTION" and task.get("output_format") == "PAGE_BLOCKS" and
             isinstance(task.get("region_formats"), dict) and "TEXT" in task["region_formats"].values()))),
            f"unknown/incompatible serialization profile {uid}")
    scope = task.get("scope")
    require(scope is None or (protocol == "PAGE_TRANSCRIPTION" and scope in PAGE_SCOPES) or (protocol != "PAGE_TRANSCRIPTION" and scope in {"SOURCE_ANNOTATED_REGION", "UPSTREAM_TARGET_REFERENCE"}),
            f"unknown/incompatible task scope {uid}")


def validate_page_scope(task, region_ids, uid, gold=None):
    """Optional legacy-compatible PAGE fields; neither field contains answers/order."""
    if "unordered_region_ids" in task or (gold is not None and "unordered_region_ids" in gold):
        unordered = task.get("unordered_region_ids")
        require(isinstance(unordered, list) and all(isinstance(r, str) for r in unordered)
                and len(unordered) == len(set(unordered)) and set(unordered) <= region_ids,
                f"invalid unordered region IDs {uid}")
        require("region_formats" in task, f"unordered PAGE requires region_formats {uid}")
        if gold is not None:
            require(gold.get("unordered_region_ids") == unordered, f"gold/input unordered region mismatch {uid}")
    if "region_formats" in task:
        formats = task["region_formats"]
        require(isinstance(formats, dict) and set(formats) == region_ids
                and all(isinstance(v,str) and v in FORMATS for v in formats.values()), f"invalid PAGE region_formats {uid}")
        if gold is not None and gold.get("blocks"):
            require(formats == {b["region_id"]: b["reference_format"] for b in gold["blocks"]},
                    f"gold/input region_formats mismatch {uid}")


def validate_predictions(predictions, inputs, selected):
    require(not (predictions.keys() - inputs.keys()), "unknown prediction sample_uid")
    require(not (predictions.keys() - set(selected)), "prediction UID outside frozen selection")
    for uid, row in predictions.items():
        page = inputs[uid]["protocol"] == "PAGE_TRANSCRIPTION"
        field = "blocks" if page else "text"
        require(set(row) == {"sample_uid", field}, f"wrong prediction fields {uid}")
        if not page:
            validate_text(row["text"])
        else:
            require(isinstance(row["blocks"], list), f"blocks must be list {uid}")
            require(sum(len(b.get("text", "")) for b in row["blocks"] if isinstance(b,dict) and isinstance(b.get("text"),str)) <= MAX_PREDICTION_VIEW_CHARACTERS, "SCORING_CAPACITY_EXCEEDED: PAGE text length")
            ids = set()
            for block in row["blocks"]:
                require(isinstance(block, dict) and set(block) == {"region_id", "text"}, f"invalid block fields {uid}")
                rid = block["region_id"]
                require(isinstance(rid, str) and rid.strip() and rid not in ids, f"invalid/duplicate block ID {rid!r} in {uid}")
                validate_text(block["text"])
                ids.add(rid)


def validate_gold(gold, inputs, selected=None, diagnostic_pending=False):
    """Check every identity/schema, then syntax only for admissible content.

    Quarantined source answers remain opaque content, never a scoring target.
    Pending content is parsed only for an explicitly requested selected diagnosis.
    """
    require(gold.keys() == inputs.keys(), "gold/input UID inventory mismatch")
    selected = set(inputs if selected is None else selected)
    for uid, row in gold.items():
        fmt = row.get("reference_format")
        expected = inputs[uid]["task"]["output_format"]
        require(fmt == ("BLOCKS" if expected == "PAGE_BLOCKS" else expected), f"gold format mismatch {uid}")
        require(type(row.get("eligible")) is bool, f"gold eligible must be boolean {uid}")
        require(row.get("review_status") in ADMITTED | {"PENDING", "QUARANTINED", "UNSUPPORTED_TASK"}, f"unknown review_status {uid}")
        require(row["review_status"] != "UNSUPPORTED_TASK" or row["eligible"] is False, f"UNSUPPORTED_TASK cannot be eligible {uid}")
        require(not row["eligible"] or row["review_status"] in ADMITTED, f"eligible unreviewed gold {uid}")
        require(isinstance(row.get("reference_text"), str), f"gold reference_text must be string {uid}")
        require(isinstance(row.get("source_binding"), dict), f"gold source_binding must be object {uid}")
        if "quarantine_reasons" in row:
            reasons = row["quarantine_reasons"]
            require(isinstance(reasons, list) and all(isinstance(r, str) and r.strip() for r in reasons), f"invalid quarantine_reasons {uid}")
        require(not row["eligible"] or (inputs[uid]["task"].get("support_status") != "UNSUPPORTED_TASK" and "UNSUPPORTED_TASK" not in row.get("quarantine_reasons", [])), f"UNSUPPORTED_TASK cannot be eligible {uid}")
        require(not row["eligible"] or bool(row["reference_text"].strip()), f"eligible reference is empty {uid}")
        quarantined = row["review_status"] == "QUARANTINED"
        check_content = row["eligible"] or (diagnostic_pending and uid in selected and row["review_status"] == "PENDING")
        if fmt == "BLOCKS":
            blocks = row.get("blocks", []) if quarantined else row.get("blocks")
            require(isinstance(blocks, list), f"gold blocks must be list {uid}")
            require(bool(blocks) or not check_content, f"gold blocks missing {uid}")
            # Only quarantined PAGE inventories may lack the entire block map.
            require(bool(blocks) or quarantined, f"gold blocks missing {uid}")
            ids = set()
            for block in blocks:
                require(isinstance(block, dict), f"invalid gold block {uid}")
                rid = block.get("region_id")
                require(isinstance(rid, str) and rid and rid not in ids, f"duplicate/invalid gold block {uid}")
                require(block.get("reference_format") in FORMATS and isinstance(block.get("text"), str), f"invalid gold block content {uid}")
                if check_content and block["reference_format"] == "TABLE_HTML":
                    parse_table(block["text"])
                elif check_content and block["reference_format"] == "LATEX":
                    require(latex_balanced(latex_tokens(block["text"])), f"unbalanced gold block LaTeX {uid}/{rid}")
                require(not check_content or bool(block["text"].strip()), f"empty eligible block {uid}/{rid}")
                ids.add(rid)
            if blocks or not quarantined:
                require(ids == {r["region_id"] for r in inputs[uid]["task"]["target_regions"]}, f"gold/input block scope mismatch {uid}")
            validate_page_scope(inputs[uid]["task"], {r["region_id"] for r in inputs[uid]["task"]["target_regions"]}, uid, row)
            if inputs[uid]["task"].get("serialization_profile") == MIXED_PROFILE:
                require(any(b["reference_format"] == "TEXT" for b in blocks), f"PAGE mixed profile requires TEXT block {uid}")
        elif check_content and fmt == "TABLE_HTML":
            parse_table(row["reference_text"])
        elif check_content and fmt == "LATEX":
            require(latex_balanced(latex_tokens(row["reference_text"])), f"unbalanced gold LaTeX {uid}")


def quarantine_reasons(row):
    """Inventory why content was excluded without altering the preserved answer."""
    return row.get("quarantine_reasons") or ["UNSPECIFIED_QUARANTINE_REASON"]


def summarize(rows):
    """Each metric denominator is within a single output format."""
    by_format = {}
    for fmt in sorted({row["reference_format"] for row in rows}):
        subset = [row for row in rows if row["reference_format"] == fmt]
        n = len(subset)
        result = dict(n=n, missing_predictions=sum(r["missing_prediction"] for r in subset),
                      exact_count=sum(r["metrics"]["exact"] for r in subset))
        result["exact_rate"] = result["exact_count"] / n
        ms = [r["metrics"] for r in subset]
        mixed = [m for m in ms if m.get("serialization_profile") == MIXED_PROFILE]
        if fmt == "TEXT" and mixed:
            profiles = defaultdict(list)
            for m in ms:
                profiles[m.get("serialization_profile", "PLAIN_TEXT_NFC_WHITESPACE_V1")].append(m)
            result["by_serialization_profile"] = {}
            for profile, metrics in sorted(profiles.items()):
                errors = sum(m["errors"] for m in metrics)
                units = sum(m["reference_units"] for m in metrics)
                result["by_serialization_profile"][profile] = dict(
                    n=len(metrics), exact_count=sum(m["exact"] for m in metrics),
                    exact_rate=sum(m["exact"] for m in metrics)/len(metrics),
                    errors=errors, reference_units=units, unit=("MIXED_SERIALIZATION_TOKENS" if profile == MIXED_PROFILE else "CHARACTERS"),
                    weighted_error_rate=errors/max(1,units))
        elif fmt in {"TEXT", "LATEX"}:
            key = "cer" if fmt == "TEXT" else "token_error_rate"
            result["macro_" + key] = sum(m[key] for m in ms) / n
            result["reference_units"] = sum(m["reference_units"] for m in ms)
            result["errors"] = sum(m["errors"] for m in ms)
            result["character_weighted_cer" if fmt == "TEXT" else "token_weighted_error_rate"] = result["errors"] / max(1, result["reference_units"])
            if fmt == "LATEX":
                result["structural_token_exact_rate"] = sum(m["structural_token_exact"] for m in ms) / n
                supported = [m for m in ms if m['layout_coverage']['reference_supported']]
                count = len(supported)
                result['layout_reference_supported_count'] = count
                result['layout_reference_unsupported_count'] = n - count
                result['layout_reference_support_rate'] = count / n
                result['layout_prediction_unsupported_on_supported_reference_count'] = sum(
                    not m['layout_coverage']['prediction_supported'] for m in supported)
                for key in ('normalized_expression_exact', 'layout_structure_exact'):
                    result[key + '_count'] = sum(m[key] is True for m in supported)
                    result[key + '_rate'] = result[key + '_count'] / count if count else None
                result['layout_denominator_scope'] = 'SUPPORTED_REFERENCES_ONLY; unsupported/missing predictions count as failures; all references retain lexical denominator'
        elif fmt == "TABLE_HTML":
            result["structure_exact_rate"] = sum(m["structure_exact"] for m in ms) / n
            for key in ("cell_text_errors", "reference_cell_characters", "reference_cells", "exact_aligned_cells", "extra_cells", "missing_cells"):
                result[key] = sum(m[key] for m in ms)
            result["macro_aligned_cell_text_cer"] = sum(m["aligned_cell_text_cer"] for m in ms) / n
            result["character_weighted_aligned_cell_text_cer"] = result["cell_text_errors"] / max(1, result["reference_cell_characters"])
        elif fmt == "BLOCKS":
            for key in ("reference_blocks", "ordered_reference_blocks", "unordered_reference_blocks", "missing_blocks", "extra_blocks", "exact_blocks", "common_block_inversions", "reference_order_pairs", "correct_order_pairs"):
                result[key] = sum(m[key] for m in ms)
            result["content_exact_rate"] = sum(m["content_exact"] for m in ms) / n
            applicable = [m for m in ms if m["order_exact"] is not None]
            result["order_applicable_pages"] = len(applicable)
            result["order_not_applicable_pages"] = n - len(applicable)
            result["order_exact_rate"] = sum(m["order_exact"] for m in applicable) / len(applicable) if applicable else None
            result["order_scope"] = "NON_EXEMPT_REFERENCE_BLOCKS_ONLY; pages with fewer than 2 are NOT_APPLICABLE"
            result["order_pair_recall"] = result["correct_order_pairs"] / result["reference_order_pairs"] if result["reference_order_pairs"] else None
            block_rows = [dict(reference_format=b["reference_format"], missing_prediction=b["missing"], metrics=b)
                          for m in ms for b in m["blocks"]]
            result["block_content_by_format"] = summarize(block_rows)
        by_format[fmt] = result
    return by_format


def stratify(rows):
    result = {}
    for field in ("protocol", "category", "source", "writing_type", "pair_condition", "task_role"):
        groups = defaultdict(list)
        for row in rows:
            groups[str(row.get(field, "ORIGINAL" if field=="task_role" else "UNKNOWN"))].append(row)
        result[field] = {key: summarize(value) for key, value in sorted(groups.items())}
    return result


def pair_report(inputs, gold, rows, selected):
    pairs, scored = defaultdict(list), {r["sample_uid"]: r for r in rows}
    for uid, row in inputs.items():
        if row["protocol"] == "PAIRED_INTERFERENCE":
            require(isinstance(row.get("pair_id"), str) and row["pair_id"], f"pair ID missing {uid}")
            pairs[row["pair_id"]].append(uid)
    report = []
    for pid, members in sorted(pairs.items()):
        if not set(members) & set(selected):
            continue
        require(len(members) == 2, f"pair must have exactly two input members {pid}")
        variants = {inputs[u].get("pair_variant") for u in members}
        require(variants in ({"crop", "context"},{"figure_removed","figure_present"}), f"pair variants invalid {pid}")
        controlled=variants=={"figure_removed","figure_present"}
        baseline,treatment=('figure_removed','figure_present') if controlled else ('crop','context')
        a, b = sorted(members, key=lambda u: inputs[u]["pair_variant"] != baseline)
        require(gold[a]["reference_format"] == gold[b]["reference_format"], f"pair gold format mismatch {pid}")
        comparison_allowed = all(gold[u]["eligible"] for u in members)
        if comparison_allowed:
            same = content_metrics(gold[a]["reference_format"], gold[a]["reference_text"], gold[b]["reference_text"], inputs[a]["task"].get("serialization_profile"), inputs[a]["task"].get("table_serialization_policy"))["exact"]
            require(same, f"pair gold targets not equivalent {pid}")
        complete = all(u in scored and not scored[u]["missing_prediction"] for u in members)
        row = dict(pair_id=pid, members=members, complete=complete,
                   pair_design='SAME_CANVAS_FIGURE_ABLATION' if controlled else 'TARGET_CROP_VS_CONTEXT',
                   baseline_condition=baseline,treatment_condition=treatment,
                   target_equivalence_checked=comparison_allowed,
                   quarantined_members=[u for u in members if gold[u]["review_status"] == "QUARANTINED"],
                   unselected_members=[u for u in members if u not in selected],
                   unscored_members=[u for u in members if u not in scored],
                   missing_prediction_members=[u for u in members if u in scored and scored[u]["missing_prediction"]])
        if complete:
            x, y = scored[a]["metrics"], scored[b]["metrics"]
            row.update(both_exact=x['exact'] and y['exact'],
                       condition_exact={baseline:x['exact'],treatment:y['exact']},
                       treatment_minus_baseline_exact=int(y['exact'])-int(x['exact']))
            if controlled:
                row.update(figure_removed_exact=x['exact'],figure_present_exact=y['exact'],
                           figure_present_minus_figure_removed_exact=int(y['exact'])-int(x['exact']))
            else:
                row.update(target_crop_exact=x['exact'],page_context_exact=y['exact'],context_minus_crop_exact=int(y['exact'])-int(x['exact']))
            key = {"TEXT": "cer", "LATEX": "token_error_rate", "TABLE_HTML": "aligned_cell_text_cer"}[gold[a]["reference_format"]]
            row["error_metric"] = key
            row['treatment_minus_baseline_error']=y[key]-x[key]
            row['figure_present_minus_figure_removed_error' if controlled else 'context_minus_crop_error']=y[key]-x[key]
        report.append(row)
    return dict(selected_pair_count=len(report), complete_pair_count=sum(r["complete"] for r in report),
                incomplete_pair_count=sum(not r["complete"] for r in report), pairs=report)


def validate_manifest(manifest, hashes, selection_hash):
    require(isinstance(manifest, dict) and manifest.get("schema_version") == 2 and manifest.get("dataset_version") == DATASET_VERSION, "invalid manifest schema/dataset version")
    model = manifest.get("model")
    require(isinstance(model, dict) and isinstance(model.get("id"), str) and model["id"].strip(), "model identity missing")
    for value, name in ((model.get("fingerprint"), "model fingerprint"), (manifest.get("config_sha256"), "config fingerprint")):
        require(isinstance(value, str) and HEX.fullmatch(value), f"invalid {name}")
    for name in ("inputs", "predictions", "config", "requests"):
        require(isinstance(manifest.get(name + "_sha256"), str) and manifest[name + "_sha256"].lower() == hashes[name], f"{name} manifest hash drift")
    require(manifest.get("selection_sha256") == selection_hash, "selection manifest hash drift")
    require(isinstance(manifest.get("created_at"), str), "created_at missing")
    try:
        timestamp = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at invalid ISO8601") from exc
    require(timestamp.tzinfo is not None, "created_at timezone required")


def score_run(root, predictions, output, run_manifest, selection=None, diagnostic_pending=False):
    root, output = Path(root).resolve(), Path(output).resolve()
    require(output != root and root not in output.parents, "scoring output must stay outside dataset root")
    require(not output.exists(), "output must not exist; immutable scoring run")
    reset_work()
    require(Path(predictions).stat().st_size<=MAX_PREDICTION_FILE_BYTES, "SCORING_CAPACITY_EXCEEDED: prediction file bytes")
    package_proof=verify_package(root)
    selection = resolve_selection(root, selection)
    paths = dict(inputs=root / "inputs/model-inputs.jsonl", gold=root / "annotations/gold.jsonl",
                 predictions=Path(predictions).resolve(), run_manifest=Path(run_manifest).resolve(), scorer=Path(__file__).resolve(),
                 latex_layout=Path(__file__).with_name('latex_layout.py').resolve())
    if selection is not None:
        paths["selection"] = Path(selection).resolve()
    paths["config"] = Path(run_manifest).resolve().parent / "config.json"
    paths["requests"] = Path(run_manifest).resolve().parent / "requests.jsonl"
    for key, path in {"records": root / "annotations/records.jsonl", "descriptor": root / "dataset.json",
                      "source_admission": Path(__file__).with_name("source_admission.py").resolve(),
                      "review_binding": Path(__file__).with_name("review_binding.py").resolve()}.items():
        if path.exists():
            paths[key] = path
    for name in ("source_admission_v21", "resolution_admission", "resolution_authority", "table_policy", "final_visual_admission", "curation_admission", "runtime_access", "package_integrity", "comparison_limits", "pair_reporting", "native_order_policy", "v24_authority"):
        path=Path(__file__).with_name(name+".py").resolve()
        if path.exists(): paths[name]=path
    data = {key: path.read_bytes() for key, path in paths.items()}
    hashes = {key: sha256(value) for key, value in data.items()}
    inputs = uid_map(jsonl(data["inputs"]), "inputs")
    require(inputs, "empty inputs")
    images = validate_inputs(inputs, root)
    selected = list(inputs)
    if selection is not None:
        sel = decode_json(data["selection"])
        require(isinstance(sel, dict) and set(sel) == {"schema_version", "inputs_sha256", "sample_uids"}, "invalid selection fields")
        require(sel["schema_version"] == 2 and sel["inputs_sha256"] == hashes["inputs"], "selection input binding drift")
        selected = sel["sample_uids"]
        require(isinstance(selected, list) and selected and all(isinstance(u, str) for u in selected), "invalid selection UID list")
        require(len(set(selected)) == len(selected) and set(selected) <= inputs.keys(), "duplicate/unknown selection UID")
    roles = {inputs[u].get("metadata", {}).get("inventory_role", "ORIGINAL") for u in selected}
    require(not ("DERIVED_LIMITED_SCOPE" in roles and len(roles) > 1),
            "derived limited-scope tasks require a separate selection and denominator")
    pred = uid_map(jsonl(data["predictions"]), "predictions")
    validate_predictions(pred, inputs, selected)
    manifest = decode_json(data["run_manifest"])
    validate_manifest(manifest, hashes, hashes.get("selection"))
    # Gold is still opaque bytes here. No answers are parsed until the seal exists.
    output.mkdir(parents=True, exist_ok=False)
    sealed = output / "sealed"
    sealed.mkdir()
    names = dict(inputs="inputs.jsonl", gold="gold.jsonl", predictions="predictions.jsonl",
                 run_manifest="run-manifest.json", scorer="scorer.py", selection="selection.json", latex_layout="latex_layout.py", records="records.jsonl",
                 descriptor="dataset.json", source_admission="source_admission.py", review_binding="review_binding.py")
    for name in ("source_admission_v21", "resolution_admission", "resolution_authority", "table_policy", "final_visual_admission", "curation_admission", "runtime_access", "package_integrity", "comparison_limits", "pair_reporting", "native_order_policy", "v24_authority"):
        names[name]=name+".py"
    names.update(config="config.json",requests="requests.jsonl")
    for key, value in data.items():
        (sealed / names[key]).write_bytes(value)
    seal = dict(schema_version=2, created_at=datetime.now(timezone.utc).isoformat(),
                hashes=hashes, original_paths={k: str(v) for k, v in paths.items()}, images=images,
                selected_sample_uids=selected, selected_count=len(selected), input_count=len(inputs),
                diagnostic_pending=bool(diagnostic_pending),
                model_provenance="caller-supplied fingerprints; no independent execution or weight attestation")
    with (output / "seal.json").open("xb") as stream:
        stream.write(json_bytes(seal))
    try:
        gold = uid_map(jsonl((sealed / names["gold"]).read_bytes()), "gold")
        validate_gold(gold, inputs, selected, diagnostic_pending)
        records = uid_map(jsonl(data["records"]), "records") if "records" in data else {}
        source_cache = {"files": {}}
        for uid in selected:
            if gold[uid]["eligible"] and (gold[uid]["review_status"] == "SOURCE_VERIFIED" or records.get(uid,{}).get("resolution_admitted") is True):
                require(uid in records, "SOURCE_VERIFIED requires source evidence record " + uid)
                validate_source_proof(root, inputs[uid], gold[uid], records[uid], cache=source_cache)
            elif gold[uid]["eligible"] and gold[uid]["review_status"] in REVIEWED:
                require(uid in records,"visual admission requires record "+uid)
                validate_visual(root,inputs[uid],gold[uid],records[uid],cache=source_cache)
        # Keep dependency bytes and hashes with this score, then recheck them
        # before publishing; no source parquet decoding or model execution.
        source_files = []
        for index, (name, digest) in enumerate(sorted(source_cache["files"].items())):
            path = Path(name)
            raw = path.read_bytes()
            require(sha256(raw) == digest, "source evidence drift before seal " + name)
            filename = "source-dependency-" + str(index) + path.suffix
            (sealed / filename).write_bytes(raw)
            source_files.append(dict(path=str(path), sha256=digest, sealed_filename=filename))
        (output / "source-evidence-seal.json").write_bytes(json_bytes(dict(files=source_files)))
        rows, diagnostic, inventory, quarantined_inventory = [], [], [], []
        for uid in selected:
            g, inp, p = gold[uid], inputs[uid], pred.get(uid)
            approved = g["eligible"] and g["review_status"] in ADMITTED
            if not approved:
                entry = dict(sample_uid=uid, eligible=g["eligible"], review_status=g["review_status"], missing_prediction=p is None)
                if g["review_status"] == "QUARANTINED":
                    entry["quarantine_reasons"] = quarantine_reasons(g)
                    quarantined_inventory.append(entry)
                inventory.append(entry)
                if not (diagnostic_pending and g["review_status"] == "PENDING"):
                    continue
            fmt = g["reference_format"]
            metric = page_metrics(g, p, inp["task"]) if fmt == "BLOCKS" else content_metrics(
                fmt, g["reference_text"], p["text"] if p else "", inp["task"].get("serialization_profile"), inp["task"].get("table_serialization_policy"))
            if p is None:
                metric["exact"] = False
                if fmt == "LATEX":
                    metric["structural_token_exact"] = False
            meta, source = inp.get("metadata", {}), g.get("source_binding", {})
            row = dict(sample_uid=uid, reference_format=fmt, protocol=inp["protocol"],
                       category="/".join(inp["category_path"]) if isinstance(inp.get("category_path"), list) else str(inp.get("category_path", "UNKNOWN")),
                       source=source.get("dataset", meta.get("source", "UNKNOWN")), writing_type=meta.get("writing_type", "UNKNOWN"),
                       pair_condition=inp.get("pair_variant") or "UNPAIRED", missing_prediction=p is None,
                       task_role=meta.get("inventory_role", "ORIGINAL"),
                       admission_status=g["review_status"], task_scope=inp["task"].get("scope"),
                       resolution_corrected=(records.get(uid,{}).get('resolution_admitted') is True and g['review_status']=='CORRECTED_REVIEWED'),
                       serialization_profile=inp["task"].get("serialization_profile"),
                       metrics=metric, diagnostic=not approved)
            (rows if approved else diagnostic).append(row)
        # Detect any concurrent upstream modification, including images, before publishing scores.
        require(all(sha256(path.read_bytes()) == hashes[key] for key, path in paths.items()), "file drift after seal")
        require(all(sha256(Path(r["path"]).read_bytes()) == r["sha256"] for r in images), "image drift after seal")
        require(all(sha256(Path(r["path"]).read_bytes()) == r["sha256"] for r in source_files), "source evidence drift after seal")
        visual_rows = [row for row in rows if row["admission_status"] in REVIEWED and not row['resolution_corrected']]
        resolution_rows = [row for row in rows if row['resolution_corrected']]
        source_rows = [row for row in rows if row["admission_status"] == "SOURCE_VERIFIED"]
        report = dict(schema_version=2, dataset_version=DATASET_VERSION, human_verified=False,
                      source_evidence_seal_sha256=sha256((output / "source-evidence-seal.json").read_bytes()),
                      admission_counts=dict(Counter(row["admission_status"] for row in rows)),
                      full_visible_content_review=dict(claimed=False, scope="SEPARATE_VISUAL_REVIEW", source_admission_is_visual_review=False), seal_sha256=sha256((output / "seal.json").read_bytes()),
                      scope="DERIVED_LIMITED_SCOPE_ONLY" if roles=={"DERIVED_LIMITED_SCOPE"} else ("EXPLICIT_SUBSET" if selection is not None else "FULL_INPUT_INVENTORY"),
                      coverage=dict(input_count=len(inputs), selected_count=len(selected), admitted_scorable_count=len(rows), reviewed_scorable_count=len(visual_rows), resolution_corrected_count=len(resolution_rows),
                                    pending_or_ineligible_count=len(inventory), admitted_missing_predictions=sum(r["missing_prediction"] for r in rows),
                                    reviewed_missing_predictions=sum(r["missing_prediction"] for r in visual_rows),
                                    pending_count=sum(gold[u]["review_status"] == "PENDING" for u in selected),
                                    unsupported_task_count=sum(gold[u]["review_status"] == "UNSUPPORTED_TASK" for u in selected),
                                    quarantined_count=len(quarantined_inventory), input_quarantined_count=sum(g["review_status"] == "QUARANTINED" for g in gold.values()),
                                    inventory=inventory), admitted=dict(by_format=summarize(rows), strata=stratify(rows),
                                    paired_interference=pair_report(inputs, gold, rows, selected)),
                      reviewed=dict(by_format=summarize(visual_rows), strata=stratify(visual_rows),
                                    paired_interference=pair_report(inputs, gold, visual_rows, selected)),
                      source_verified=dict(by_format=summarize(source_rows), strata=stratify(source_rows),
                                           visual_review_claimed=False, human_verified=False),
                      resolution_corrected=dict(count=len(resolution_rows),by_format=summarize(resolution_rows),strata=stratify(resolution_rows),
                                                full_target_visual_review_claimed=False,human_verified=False,
                                                scope='LOCAL_CORRECTION_OR_SERIALIZATION_POLICY_WITH_REMAINING_CONTENT_BOUND_TO_SOURCE'),
                      diagnostic_pending=dict(enabled=bool(diagnostic_pending), by_format=summarize(diagnostic), strata=stratify(diagnostic)),
                      unsupported_tasks=dict(count=sum(gold[u]["review_status"] == "UNSUPPORTED_TASK" for u in selected),
                                             sample_uids=[u for u in selected if gold[u]["review_status"] == "UNSUPPORTED_TASK"], scored=False),
                      quarantined=dict(count=len(quarantined_inventory), inventory=quarantined_inventory, scored=False),
                      limitations=["No heterogeneous global accuracy.", "LaTeX exact/errors preserve the lexical token metric; normalized expression/layout metrics use explicit supported-reference coverage, not mathematical semantic equivalence.",
                                   "SOURCE_VERIFIED checks frozen original-source audits and exact baseline transfer; it does not claim visual or human review. Model execution remains caller-attested.",
                                   "MIXED upstream TeX controls, scripts, punctuation, digits and Chinese are retained lexically; no prose stripping or algebraic equivalence.",
                                   "Missing predictions remain in the applicable selected denominator."])
        access=admission_state(root)["access"]
        access.verify()
        report["scope"]=selection_scope(selection)
        report["package_integrity"]=package_proof
        report["runtime_bindings"]=access.frozen
        report["model_execution_attested"]=False
        report["pair_dependence"]=dependence_summary(inputs,gold,report["admitted"]["paired_interference"])
        report["text_metric_policy"]="TEXT profiles are stratified; no combined CER across character and mixed-token units."
        with (output / "rows.jsonl").open("xb") as stream:
            for row in rows + diagnostic:
                stream.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        with (output / "results.json").open("xb") as stream:
            stream.write(json_bytes(report))
        return report
    except Exception as exc:
        with (output / "failure.json").open("xb") as stream:
            stream.write(json_bytes(dict(error_type=type(exc).__name__, error=str(exc), sealed=True)))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("root", "predictions", "output", "run-manifest"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--selection", help="default: selections/admitted-all.json when present; reviewed-all is a compatibility alias")
    parser.add_argument("--diagnostic-pending", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = score_run(args.root, args.predictions, args.output, args.run_manifest, args.selection, args.diagnostic_pending)
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError, AttributeError, RecursionError, OverflowError) as exc:
        parser.exit(2, json.dumps(dict(passed=False,error_type=type(exc).__name__,error=str(exc)),ensure_ascii=False)+chr(10))
    print(json.dumps(dict(output=str(Path(args.output).resolve()), coverage=result["coverage"], scope=result["scope"]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
