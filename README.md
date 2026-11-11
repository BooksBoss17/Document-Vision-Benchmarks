# Education Knowledge Base Benchmarks

面向图片语义分类、裁图转录、文档区域转录与公开来源转录的评测工具、协议和实测报告。Python 3.11+；不加载模型、不包含权重。

**代码与汇总报告采用 Apache-2.0。原教材、试卷、受限上游图片、参考原文、逐项预测及内部日志不在公开发行范围。** 运行真实评测需要自行取得对应完整数据包及授权；本仓库没有把缩小后的示例当作原评测集。

## 选择评测任务

| 工具标识 | 原评测版本与范围 | 数据状态 |
|---|---|---|
| `image-classification` | 语义联系 v1.2.0；8767 用途视图、8564 唯一图像、12 本书 | 教材衍生数据另行授权，本仓库不附带 |
| `transcription-crops` | v1.1.1；默认434项、完整435项；待核66、隔离443不评分 | 去名修订不改变原著作权，完整包另行授权 |
| `document-regions` | v1.1.0；200区域，默认195可评分 | 教材/试卷及其原文另行授权 |
| `public-transcription` | v2.4.0；3039库存视图、默认2928 | 混合上游来源，包含限制或不明确条款；完整数据包不再分发 |
| `public-quick` | v2.1.0；480视图，440区域、8页、16组完整干扰对 | 完整依赖v2.4父包；不是独立新增480个目标 |

机器登记见 [benchmarks.json](benchmarks.json)，任务及数据格式见 [协议说明](PROTOCOLS.md)，授权与获取边界见 [数据来源](DATA_ACCESS.md)。教材组织的部分完成材料、历史实验对照和退役版本不冒充现行正式评测集。

## 先检查安装

克隆本仓库，在仓库根目录运行：

```sh
python -B ekb_bench.py list
python -B ekb_bench.py doctor
python -m pip install -r requirements-metrics.txt
python -B -m unittest discover -s tests -v
```

列表、代码完整性与依赖访问自测只需要Python标准库；全部自测还包含报告内容指标，需要NumPy。完整图像/文档校验可在独立虚拟环境安装 [requirements-validation.txt](requirements-validation.txt)。这些是评测端依赖，不是六个OCR模型的推理环境。

## 使用自有授权的完整数据包

将完整包放在仓库外，输出也放在数据包外的新目录。下例的路径须替换为自己持有的本地数据路径；原始金标不发送给模型。

```sh
python -B ekb_bench.py run document-regions verify --data-root /path/to/document-regions-v1.1
python -B ekb_bench.py run document-regions score --data-root /path/to/document-regions-v1.1 --predictions /path/to/predictions.jsonl --output /path/to/new-score --view default
```

公开转录原包还需要单独授权的来源证明与原件。使用 [便携依赖索引](DATA_ACCESS.md#便携依赖索引) 显式接入；无需本项目开发目录、个人用户名、MCP安装或NAS映射。

```sh
python -B ekb_bench.py run public-transcription verify --data-root /path/to/public-v2.4
python -B ekb_bench.py run public-transcription validate --data-root /path/to/public-v2.4 --dependencies /path/to/licensed-dependencies/index.json --output /path/to/new-validation.json
python -B ekb_bench.py run public-quick validate --data-root /path/to/quick-v2 --parent-root /path/to/public-v2.4 --output /path/to/new-quick-validation.json
```

`public-quick prepare/score` 自动固定到原包的完整480视图selection。`public-transcription` 默认仍为原2928视图；子集必须显式声明，不能以子集成绩代替默认全量。使用相同输入、模型/配置和selection准备、封存与评分；更换运行器后创建新封存，不重新签署旧模型预测来伪造新执行。

`verify`核对数据包身份/完整性，`validate`还执行对应来源、结构与像素规则；两者都不等于全文人工认证。缺少数据或证据时明确失败，不跳过来源准入、不降低哈希要求。

## 六模型实测报告

[报告目录](reports/2026-11-11-six-models/README.md)包含：

- [文档区域转录v1.1：195区域](reports/2026-11-11-six-models/document-regions-v1.1.md)
- [公开快速集v2：480视图](reports/2026-11-11-six-models/public-quick-v2.md)

两份报告使用不同任务与归一化协议，不能合并排名。历史暴露、AI辅助参考、Windows原生模型适配、资源失败和计时边界均在报告中保留。本次发布复用已封存结果，没有重跑模型。

快速集报告使用的内容归一化与精确DP函数也按冻结源码公开在`vendor/report-content-metrics/`。`python -B metrics.py --reference-file R.txt --prediction-file P.txt --format LATEX --output new-metric.json`可核对单段自有文本；这是内容指标工具，不执行数据包来源准入或模型推理。原报告的逐模型、逐视图分组与封存顺序仍须按协议保留。

## 代码与发行身份

`vendor/`保留各任务的既有评分、准备与验证实现；公开转录额外提供便携依赖访问适配。完整数据包身份和原金标不随适配变化，公开代码另有独立发行指纹；原工具身份对应见 [SOURCE_PROVENANCE.json](SOURCE_PROVENANCE.json)。发行文件由 [release-manifest.json](release-manifest.json) 校验。

请只提交工具、协议、测试与可公开汇总。数据、依赖对象、模型、缓存和运行输出保持在仓库外，不把程序许可证套用到第三方材料上。
