"""Deterministic local v2.4 subset construction and exact-subset validation. CPU only."""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
SEED = "public-quick-v2-resume-20261111-v1"
FORBIDDEN = {"reference_text", "answer", "gold", "expected_answer", "expected_text",
             "eligible", "review_status", "review_evidence", "source_evidence",
             "source_admission", "reference_sha256", "review_binding"}


def need(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, "duplicate JSON key: " + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique)


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lines(path):
    result, raw = {}, {}
    for line in Path(path).read_bytes().splitlines(keepends=True):
        if not line.strip():
            continue
        row = json.loads(line)
        uid = row["sample_uid"]
        need(uid not in result, "duplicate UID: " + uid)
        result[uid], raw[uid] = row, line
    return result, raw


def safe(root, relative):
    p = Path(relative)
    target = (root / p).resolve()
    need(not p.is_absolute() and ".." not in p.parts and target.is_relative_to(root.resolve()),
         "unsafe subset member path: " + relative)
    need(not (root / p).is_symlink(), "symlink member: " + relative)
    return target


def no_answers(value):
    if isinstance(value, dict):
        need(not FORBIDDEN.intersection(value), "answer/review fields in public input")
        for child in value.values():
            no_answers(child)
    elif isinstance(value, list):
        for child in value:
            no_answers(child)


def verify_parent(parent, bindings):
    for relative, expected in bindings.items():
        need(sha(safe(parent, relative)) == expected, "parent fingerprint drift: " + relative)


def rank(uid):
    return hashlib.sha256((SEED + "\0" + uid).encode("utf-8")).hexdigest()


def source(row):
    return row["metadata"]["source_dataset"]


def source_page(row, records):
    binding = records[row["sample_uid"]]["source_binding"]
    return binding.get("page_group_id") or binding.get("source_id") or row["sample_uid"]


def document(row, records):
    value = records[row["sample_uid"]]["source_binding"].get("document_group_id")
    return value if value and "UNKNOWN" not in value else None


def features(row):
    meta, task = row["metadata"], row["task"]
    return (row["category_path"], meta.get("language", "UNKNOWN"),
            meta.get("writing_type", "UNKNOWN"), task.get("serialization_profile", "PLAIN"),
            task.get("annotation_policy", "DEFAULT"),
            min(7, int(math.log2(max(1, row["width"] * row["height"]))) // 3))


def choose(pool, count, selected, records, predicate=None):
    """Page diversity first, then declared marginal strata; answer text is never consulted."""
    need(len(selected) <= count, "mandatory members exceed quota")
    by_uid = {x["sample_uid"]: x for x in pool}
    need(set(selected) <= by_uid.keys(), "mandatory member outside stratum")
    while len(selected) < count:
        used = [by_uid[u] for u in selected]
        pages = collections.Counter(source_page(x, records) for x in used)
        docs = collections.Counter(document(x, records) for x in used if document(x, records))
        strata = [collections.Counter(features(x)[i] for x in used) for i in range(6)]
        remaining = [x for x in pool if x["sample_uid"] not in selected and (predicate is None or predicate(x, used))]
        need(remaining, "selection quota infeasible")
        def key(x):
            doc = document(x, records)
            return (pages[source_page(x, records)], docs[doc] if doc else 0,
                    sum(strata[i][v] for i, v in enumerate(features(x))), rank(x["sample_uid"]))
        selected.add(min(remaining, key=key)["sample_uid"])
    return selected


def select(parent, plan, anchors):
    inputs, _ = lines(parent / "inputs/model-inputs.jsonl")
    records, _ = lines(parent / "annotations/records.jsonl")
    admitted = set(read(parent / "selections/admitted-all.json")["sample_uids"])
    need(set(anchors) <= admitted, "old anchor no longer admitted")
    rows = [inputs[u] for u in sorted(admitted)]
    pair_map = collections.defaultdict(list)
    for x in rows:
        if x["protocol"] == "PAIRED_INTERFERENCE":
            pair_map[x["pair_id"]].append(x)
    core_by_legacy = {x["legacy_uid"]: x for x in rows if x["protocol"] == "REGION_TRANSCRIPTION"}
    complete = {}
    for pid, members in pair_map.items():
        if len(members) == 2 and {x["pair_variant"] for x in members} == {"figure_present", "figure_removed"}:
            present = next(x for x in members if x["pair_variant"] == "figure_present")
            if present["legacy_uid"] in core_by_legacy:
                complete[pid] = present
    anchor_pairs = {inputs[u]["pair_id"] for u in anchors if inputs[u]["pair_id"]}
    need(anchor_pairs <= complete.keys(), "anchor pair lacks admitted region")
    pairs = set(anchor_pairs)
    def stimulus(row):
        return tuple(sorted(x["image_sha256"] for x in pair_map[row["pair_id"]]))
    # Distinct targets may intentionally share a stimulus. Only frozen anchor collisions survive.
    anchor_stimuli = collections.defaultdict(set)
    for pid in pairs:
        anchor_stimuli[stimulus(complete[pid])].add(pid)
    collisions = sorted(sorted(v) for v in anchor_stimuli.values() if len(v) > 1)
    need(collisions == sorted(plan.get("allowed_shared_stimulus_anchor_groups", [])), "undeclared anchor pair pixel collision")
    for fmt, count in plan["pair_quotas"].items():
        pool = [x for x in complete.values() if x["task"]["output_format"] == fmt]
        selected = {complete[p]["sample_uid"] for p in pairs if complete[p]["task"]["output_format"] == fmt}
        # Enforce unique stimuli across all formats as well as within the current format.
        other_stimuli = {stimulus(complete[p]) for p in pairs if complete[p]["task"]["output_format"] != fmt}
        selected = choose(pool, count, selected, records,
                          lambda x, used: stimulus(x) not in other_stimuli | {stimulus(y) for y in used})
        pairs.update(inputs[u]["pair_id"] for u in selected)
    chosen = {u for u in anchors if inputs[u]["protocol"] == "REGION_TRANSCRIPTION"}
    chosen.update(core_by_legacy[complete[p]["legacy_uid"]]["sample_uid"] for p in pairs)
    chosen.update(x["sample_uid"] for x in rows if x["protocol"] == "REGION_TRANSCRIPTION"
                  and x["task"].get("annotation_policy") == "CASIA_NATIVE_LABELLED_TEXT_V1")
    for fmt, sources in plan["region_quotas"].items():
        for src, count in sources.items():
            pool = [x for x in rows if x["protocol"] == "REGION_TRANSCRIPTION"
                    and x["task"]["output_format"] == fmt and source(x) == src]
            mandatory = {u for u in chosen if inputs[u] in pool}
            # Mixed serialization has its own source-annotated contract. Reserve 12 Exam examples.
            if fmt == "TEXT" and src == "examocrbench":
                mixed = [x for x in pool if x["task"].get("serialization_profile")]
                reserved = {u for u in mandatory if inputs[u]["task"].get("serialization_profile")}
                mandatory.update(choose(mixed, max(12, len(reserved)), reserved, records))
            chosen.update(choose(pool, count, mandatory, records))
    for src, count in plan["page_quotas"].items():
        mandatory = {u for u in anchors if inputs[u]["protocol"] == "PAGE_TRANSCRIPTION" and source(inputs[u]) == src}
        pool = [x for x in rows if x["protocol"] == "PAGE_TRANSCRIPTION" and source(x) == src
                and (len(x["task"]["target_regions"]) <= 20 or x["sample_uid"] in mandatory)]
        # Keep both small (<=8) and medium (9..20) Omni pages for a bounded whole-page probe.
        if src == "omnidocbench" and not any(9 <= len(inputs[u]["task"]["target_regions"]) <= 20 for u in mandatory):
            medium = [x for x in pool if 9 <= len(x["task"]["target_regions"]) <= 20]
            mandatory.update(choose(medium, 1, set(), records))
        chosen.update(choose(pool, count, mandatory, records))
    chosen.update(x["sample_uid"] for pid in pairs for x in pair_map[pid])
    need(len(chosen) == 480 and set(anchors) <= chosen, "selection size/anchor failure")
    return sorted(chosen), inputs, records


def counts(values):
    return dict(sorted(collections.Counter(values).items()))


def statistics(uids, inputs, records):
    rows = [inputs[u] for u in uids]
    core = [x for x in rows if x["protocol"] == "REGION_TRANSCRIPTION"]
    pairs = [x for x in rows if x["protocol"] == "PAIRED_INTERFERENCE"]
    pages = [x for x in rows if x["protocol"] == "PAGE_TRANSCRIPTION"]
    docs = {document(x, records) for x in rows} - {None}
    legacy_regions = {x["legacy_uid"] for x in core}
    return {"task_views": len(rows), "by_protocol": counts(x["protocol"] for x in rows),
            "region_by_format": counts(x["task"]["output_format"] for x in core),
            "region_by_source_format": counts(source(x) + "/" + x["task"]["output_format"] for x in core),
            "by_source": counts(source(x) for x in rows),
            "region_by_category": counts(x["category_path"] for x in core),
            "region_by_writing_type": counts(x["metadata"]["writing_type"] for x in core),
            "region_by_language": counts(x["metadata"]["language"] for x in core),
            "region_by_serialization_profile": counts(x["task"].get("serialization_profile", "PLAIN") for x in core),
            "region_annotation_policies": counts(x["task"].get("annotation_policy", "DEFAULT") for x in core),
            "page_block_counts": {x["sample_uid"]: len(x["task"]["target_regions"]) for x in pages},
            "page_by_source": counts(source(x) for x in pages),
            "pair_by_format": counts(x["task"]["output_format"] for x in pairs if x["pair_variant"] == "figure_present"),
            "complete_pairs": len({x["pair_id"] for x in pairs}),
            "distinct_pair_pixel_stimuli": len({tuple(sorted(y["image_sha256"] for y in pairs if y["pair_id"] == x["pair_id"])) for x in pairs}),
            "pair_targets_with_selected_region": len({x["legacy_uid"] for x in pairs} & legacy_regions),
            "distinct_region_image_sha256": len({x["image_sha256"] for x in core}),
            "unique_image_sha256": len({x["image_sha256"] for x in rows}),
            "unique_image_paths": len({x["image"] for x in rows}),
            "distinct_source_page_groups": len({source_page(x, records) for x in rows}),
            "distinct_region_source_page_groups": len({source_page(x, records) for x in core}),
            "nonnull_known_document_groups": len(docs),
            "review_status": counts(records[u]["review_status"] for u in uids),
            "source_page_note": "A task view/crop is not an independent page/document; pair targets overlap their selected REGION counterpart."}


def freeze(root):
    members = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "checksums.sha256")
    (root / "checksums.sha256").write_text("".join(sha(p) + "  " + p.relative_to(root).as_posix() + "\n" for p in members), encoding="utf-8")


def build(parent, output, plan_path, anchors_path):
    parent, output = parent.resolve(), output.resolve()
    need(not output.exists() and not output.is_relative_to(parent), "fresh candidate outside parent required")
    plan, anchors = read(plan_path), read(anchors_path)["sample_uids"]
    verify_parent(parent, plan["parent_sha256"])
    uids, inputs, records = select(parent, plan, anchors)
    output.mkdir(parents=True)
    selection = {"schema_version": 2, "inputs_sha256": sha(parent / "inputs/model-inputs.jsonl"), "sample_uids": uids}
    write(output / "selection.json", selection)
    write(output / "anchors.json", {"schema_version": 2, "inputs_sha256": selection["inputs_sha256"], "sample_uids": sorted(anchors)})
    # These are exact subsets of the parent's original lines, not regenerated gold.
    for rel in ["inputs/model-inputs.jsonl", "annotations/gold.jsonl", "annotations/records.jsonl"]:
        _, raw = lines(parent / rel)
        target = output / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"".join(raw[u] for u in uids))
    catalogue = []
    for uid in uids:
        row, record = inputs[uid], records[uid]
        dest = safe(output, row["image"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(safe(parent, row["image"]), dest)
        catalogue.append({"sample_uid": uid, "parent_sample_uid": uid, "image": row["image"],
                          "image_sha256": row["image_sha256"], "protocol": row["protocol"],
                          "source_binding": record["source_binding"], "parent_source_evidence": record.get("source_evidence"),
                          "category_path": row["category_path"], "pair_id": row["pair_id"],
                          "pair_variant": row["pair_variant"], "legacy_uid": row["legacy_uid"]})
    (output / "SOURCE_CATALOG.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in catalogue), encoding="utf-8")
    stats = statistics(uids, inputs, records)
    write(output / "STATISTICS.json", stats)
    config = {"schema": "public-transcription-quick-v2", "revision": "2.1.0", "dataset_version": "2.4.0",
              "dataset": "标准评测集/公开权威转录标准评测集_v2.4", "parent_sha256": plan["parent_sha256"],
              "quick_selection_sha256": sha(output / "selection.json"), "task_views": 480,
              "sampling_seed": SEED, "region_quotas": plan["region_quotas"], "page_quotas": plan["page_quotas"],
              "pair_quotas": plan["pair_quotas"], "lower_bound": plan["floor"],
              "allowed_shared_stimulus_anchor_groups": plan.get("allowed_shared_stimulus_anchor_groups", []),
              "anchor_count": len(anchors), "historically_exposed": True, "human_verified": False,
              "quality_acceptance": False, "scope": "CUSTOM_SUBSET_NOT_OFFICIAL_SCORE",
              "materialized_subset": True, "official_runtime_requires_parent_v24": True,
              "new_model_calls": 0, "model_accuracy_tested_in_this_task": False,
              "historical_prediction_or_five_request_gates_reused": False,
              "selection_policy": plan["selection_method"],
              "content_priority": "Recognized characters and mathematical symbols/structure; no new format-match acceptance threshold. Parent native metrics remain explicitly separate.",
              "source_evidence_base": "PARENT_V24_ROOT; selected records retain original source/evidence paths"}
    write(output / "config.json", config)
    write(output / "dataset.json", {"name": "公开转录快速测试集_v2", "version": "2.1.0", "parent_version": "2.4.0",
                                    "total_samples": 480, "sample_count_unit": "TASK_VIEWS",
                                    "region_samples": 440, "page_samples": 8, "complete_pairs": 16,
                                    "human_verified": False, "historically_exposed": True,
                                    "full_text_certification": False, "model_quality_tested": False,
                                    "selection": "selection.json", "model_inputs": "inputs/model-inputs.jsonl",
                                    "gold": "annotations/gold.jsonl", "validator": "tools/quick_dataset.py",
                                    "official_runtime_parent": "../公开权威转录标准评测集_v2.4"})
    shutil.copyfile(parent / "UPSTREAM_LICENSES.md", output / "UPSTREAM_LICENSES.md")
    (output / "tools").mkdir()
    shutil.copyfile(Path(__file__), output / "tools/quick_dataset.py")
    (output / "README.md").write_text(readme(stats), encoding="utf-8")
    verify_parent(parent, plan["parent_sha256"])
    freeze(output)
    return {"passed": True, "output": str(output), "manifest_sha256": sha(output / "checksums.sha256"), "statistics": stats}


def readme(stats):
    return f'''# 公开转录快速测试集 v2

当前修订2.1.0，来源公开权威转录标准评测集v2.4.0。共480个任务视图：440个区域、8个整页、16组干扰对32视图。区域有440张不同图像，独立满足转录标准评测集v1.1有效435项的规模下限；不是用成对视图凑下限。相对父集默认2928视图，本集约16.4%，不代表等比例或统计无偏抽样。

区域分为文字260、公式140、表格40；五来源均有数据。原28个回归锚点全保留，另增加452视图。逐层分布见[STATISTICS.json](STATISTICS.json)，逐项来源见[SOURCE_CATALOG.jsonl](SOURCE_CATALOG.jsonl)，固定UID见[selection.json](selection.json)，原28项见[anchors.json](anchors.json)。

## 使用范围

这是历史暴露的日常诊断与回归子集，human_verified=false；没有新做480项全文人工复核或模型质量测试。来源准入与原有审阅沿用v2.4，金标、输入和图片均不改写。视图不等于独立页：所选数据来自{stats['distinct_source_page_groups']}个来源页组，区域来自{stats['distinct_region_source_page_groups']}个来源页组；16组配对目标全部同时包含在440个区域中，应分开报告区域与上下文干扰表现，不把重复目标当独立证据。

选样按预先锁定的来源/格式配额、固定哈希种子和页/类别/语言分散规则完成，未使用模型成绩或答案难度。整页只有8项，新增页最多20个目标块，不能据此认证整页能力。干扰对是相同画布的白底图窗消融，保留目标像素；不等于自然无图页面，未新增干扰强度门槛。原有公式D1F-0001与表格D1T-0002对照共用画布但目标不同，保留为两个回归任务，16组对照实际只有{stats['distinct_pair_pixel_stimuli']}组不同图像刺激；新增组不复用任何已选刺激。

## 文件与答案隔离

- `inputs/model-inputs.jsonl`：480条原始无答案任务输入。图片路径相对此目录，图片已按父集原字节物化，支持直接查看或输入自建运行器。
- `annotations/gold.jsonl`、`annotations/records.jsonl`：父集所选行的原字节子集，只供冻结预测后的评分/审计。记录内证据相对路径仍以父集v2.4为基准，并未复制整套上游证据。
- `selection.json`：绑定父集**完整输入文件**的SHA，用于父集原生prepare/score；不是绑定本集480行输入文件的SHA，二者不要混用。
- `config.json`、`dataset.json`、`checksums.sha256`：来源指纹、范围、配额和本包全部文件校验。

## 校验与运行

在开发区根目录执行。`<python>`使用现有含Pillow的Python环境，例如`tmp/ocrv11/envs/latest/Scripts/python.exe`。所有运行输出放在本包之外的新目录。

```powershell
<python> -B -X utf8 "标准评测集/公开转录快速测试集_v2/tools/quick_dataset.py" validate --root "标准评测集/公开转录快速测试集_v2" --parent "标准评测集/公开权威转录标准评测集_v2.4" --output "tmp/runs/<new-run>/quick-validation.json"
<python> -B -X utf8 "标准评测集/公开权威转录标准评测集_v2.4/tools/prepare_run.py" prepare --root "标准评测集/公开权威转录标准评测集_v2.4" --selection "标准评测集/公开转录快速测试集_v2/selection.json" --output "tmp/runs/<new-run>/prepared" --model-id "<actual-model-id>" --model-fingerprint "<actual-64hex-sha256>" --config "标准评测集/公开转录快速测试集_v2/config.json"
<python> -B -X utf8 "标准评测集/公开权威转录标准评测集_v2.4/tools/prepare_run.py" bind --prepared "tmp/runs/<new-run>/prepared" --predictions "tmp/runs/<new-run>/raw-predictions.jsonl"
<python> -B -X utf8 scripts/evaluation/score_public_transcription_v24.py score --selection "标准评测集/公开转录快速测试集_v2/selection.json" --predictions "tmp/runs/<new-run>/prepared/predictions.jsonl" --run-manifest "tmp/runs/<new-run>/prepared/run-manifest.json" --output "tmp/runs/<new-run>/score"
```

模型只读取prepare生成的`requests.jsonl`及其图片，切勿把本包金标/审阅记录输入模型。正式原生运行仍依赖完整的、已注册的v2.4父包和其可定位的来源证据，不能将本物化子集伪装成完整v2.4包。prepare生成的图片路径指向父包，像素与本集副本完全一致。prepare与score必须使用同一份selection；每次真实模型运行重新准备/绑定，原v1耗时、旧五请求门禁不用于本修订。

本用户关注字、符号和数学结构是否识别正确，不新增格式匹配达标门槛。父包原生输出包含文字字符/混合TeX分层、公式保守token及支持域内布局、表格单元格文字、整页块内容等诊断；其中格式、表结构和阅读顺序指标应单列，不当作“字转对”结论。内容等价评分须另明确规范和实际使用的适配器，不能把原生token严格一致率直接改名为内容准确率。本次只验证建包与运行机制，不生成任何OCR成绩。

缺失或失败预测保留在所选分母；配对必须双视图，按16组报告。父包原生结果标识`CUSTOM_SUBSET_NOT_OFFICIAL_SCORE`，不得宣称完成v2.4全量2928视图。哈希、包校验与合成控制通过均不代表模型识别正确率通过。

## 许可与续接

“公开”描述数据来源，不代表可对外再分发。上游科研/非商业及HME100K、ExamOCRBench等限制继续有效，详见[UPSTREAM_LICENSES.md](UPSTREAM_LICENSES.md)。本次仅开发区本地整理，未发布MCP或GitHub。

构建方案、被中断成果快照与验收见[本次运行](../../tmp/runs/public-transcription-quick-v2-resume-20261111/REPORT.md)。旧28项的README/config/selection原字节保存在该运行`baseline/quick-v2`，旧历史证据未覆盖。
'''


def validate(root, parent):
    from PIL import Image, ImageChops
    root, parent = root.resolve(), parent.resolve()
    config = read(root / "config.json")
    verify_parent(parent, config["parent_sha256"])
    expected = {}
    for line in (root / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        need(rel not in expected and len(digest) == 64, "invalid/duplicate package checksum")
        need(sha(safe(root, rel)) == digest, "subset member drift: " + rel)
        expected[rel] = digest
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p != root / "checksums.sha256"}
    need(set(expected) == actual, "subset package coverage mismatch")
    selection = read(root / "selection.json")
    uids = selection["sample_uids"]
    need(selection["schema_version"] == 2 and selection["inputs_sha256"] == sha(parent / "inputs/model-inputs.jsonl"), "selection input binding")
    need(len(uids) == len(set(uids)) == 480 and uids == sorted(uids), "480 sorted unique UIDs required")
    admitted = set(read(parent / "selections/admitted-all.json")["sample_uids"])
    anchors = read(root / "anchors.json")["sample_uids"]
    need(set(uids) <= admitted and len(anchors) == len(set(anchors)) == 28 and set(anchors) <= set(uids), "admission/anchor coverage")
    parent_inputs, _ = lines(parent / "inputs/model-inputs.jsonl")
    parent_records, _ = lines(parent / "annotations/records.jsonl")
    for rel in ["inputs/model-inputs.jsonl", "annotations/gold.jsonl", "annotations/records.jsonl"]:
        _, raw = lines(parent / rel)
        subset, _ = lines(root / rel)
        need(set(subset) == set(uids) and (root / rel).read_bytes() == b"".join(raw[u] for u in uids), "not exact parent line subset: " + rel)
    stats = statistics(uids, parent_inputs, parent_records)
    need(stats == read(root / "STATISTICS.json"), "statistics drift")
    need(stats["by_protocol"] == {"REGION_TRANSCRIPTION": 440, "PAGE_TRANSCRIPTION": 8, "PAIRED_INTERFERENCE": 32}, "protocol quota")
    need(stats["distinct_region_image_sha256"] == 440 >= config["lower_bound"]["active_eligible"], "region floor not met")
    quota = {src + "/" + fmt: n for fmt, srcs in config["region_quotas"].items() for src, n in srcs.items()}
    need(stats["region_by_source_format"] == quota and stats["page_by_source"] == config["page_quotas"] and stats["pair_by_format"] == config["pair_quotas"], "source/format quotas")
    need(stats["pair_targets_with_selected_region"] == 16 and len(stats["by_source"]) == 5, "target/source coverage")
    special = {u for u in admitted if parent_inputs[u]["protocol"] == "REGION_TRANSCRIPTION" and parent_inputs[u]["task"].get("annotation_policy") == "CASIA_NATIVE_LABELLED_TEXT_V1"}
    need(len(special) == 4 and special <= set(uids), "CASIA policy coverage")
    need(stats["region_by_serialization_profile"].get("UPSTREAM_TEX_MIXED_V1", 0) >= 12, "mixed profile coverage")
    checked = set()
    pairs = collections.defaultdict(list)
    for uid in uids:
        row = parent_inputs[uid]
        no_answers(row)
        rel = row["image"]
        if rel not in checked:
            need(sha(safe(root, rel)) == sha(safe(parent, rel)) == row["image_sha256"], "copied image differs: " + uid)
            with Image.open(root / rel) as im:
                im.load()
                need(im.size == (row["width"], row["height"]), "image geometry: " + uid)
            checked.add(rel)
        if row["protocol"] == "PAIRED_INTERFERENCE":
            pairs[row["pair_id"]].append(row)
        if row["protocol"] == "PAGE_TRANSCRIPTION" and uid not in anchors:
            need(len(row["task"]["target_regions"]) <= 20, "added PAGE block budget")
    stimuli = collections.defaultdict(set)
    for pid, members in pairs.items():
        need(len(members) == 2 and {x["pair_variant"] for x in members} == {"figure_present", "figure_removed"}, "incomplete pair: " + pid)
        stimulus = tuple(sorted(x["image_sha256"] for x in members))
        stimuli[stimulus].add(pid)
        a, b = sorted(members, key=lambda x: x["pair_variant"])
        need(a["task"] == b["task"] and (a["width"], a["height"]) == (b["width"], b["height"]), "pair contract/geometry")
        with Image.open(root / a["image"]) as left, Image.open(root / b["image"]) as right:
            for region in a["task"]["target_regions"]:
                x0, y0, x1, y1 = region["bbox"]
                box = (max(0, math.floor(x0)), max(0, math.floor(y0)), min(left.width, math.ceil(x1)), min(left.height, math.ceil(y1)))
                with left.crop(box).convert("RGB") as ca, right.crop(box).convert("RGB") as cb:
                    with ImageChops.difference(ca, cb) as diff:
                        need(diff.getbbox() is None, "pair target pixels changed: " + pid)
    collisions = sorted(sorted(v) for v in stimuli.values() if len(v) > 1)
    need(collisions == sorted(config.get("allowed_shared_stimulus_anchor_groups", []))
         and len(stimuli) == 15, "undeclared repeated pair pixel stimulus")
    catalogue, _ = lines(root / "SOURCE_CATALOG.jsonl")
    need(set(catalogue) == set(uids), "catalogue membership")
    for uid in uids:
        c, r = catalogue[uid], parent_records[uid]
        need(c["source_binding"] == r["source_binding"] and c["parent_source_evidence"] == r.get("source_evidence")
             and c["image_sha256"] == parent_inputs[uid]["image_sha256"], "catalogue lineage drift: " + uid)
    verify_parent(parent, config["parent_sha256"])
    return {"passed": True, "manifest_sha256": sha(root / "checksums.sha256"),
            "checked_task_views": len(uids), "decoded_image_paths": len(checked), "protected_pixel_pairs": len(pairs),
            "exact_parent_subset": True, "answer_free_inputs": True, "statistics": stats,
            "model_execution_attested": False, "full_text_certification": False}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sp = ap.add_subparsers(dest="action", required=True)
    p = sp.add_parser("build")
    for name in ("parent", "output", "plan", "anchors"):
        p.add_argument("--" + name, type=Path, required=True)
    p = sp.add_parser("validate")
    for name in ("root", "parent", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    try:
        if args.action == "build":
            result = build(args.parent, args.output, args.plan, args.anchors)
        else:
            need(not args.output.resolve().is_relative_to(args.root.resolve()), "validation receipt must stay outside package")
            need(not args.output.exists(), "fresh validation receipt required")
            result = validate(args.root, args.parent)
            write(args.output, result)
        print(json.dumps({k: v for k, v in result.items() if k != "statistics"}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"passed": False, "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
