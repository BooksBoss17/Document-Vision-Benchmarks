# 数据来源、授权与本地接入

本仓库公开工具、协议和汇总报告。维护者未持有私有教材/试卷及全部上游材料的公开再分发授权，因此本发行不包含真实源图、原文、金标、逐项OCR输出或其派生缓存。去名、裁图、重编码、换文件名和只公开部分样本，都不会自动取得新的数据许可。

## 原始来源

| 来源 | 官方入口 | 本发行处理 |
|---|---|---|
| 教材、试卷及其裁图/标注 | 依原出版者和权利人授权 | 完整包另行授权，不上传 |
| OmniDocBench | [官方项目](https://github.com/opendatalab/OmniDocBench) | 代码许可与数据版权声明分开；混合文档材料不统一改授Apache许可 |
| OCRBench v2 | [官方说明](https://github.com/Yuliang-Liu/MultimodalOCR/tree/main/OCRBench_v2) | 研究/非商业与多来源材料限制须单独核对，不以数据卡上的简短标签扩大权利 |
| CASIA-HWDB 2.x | [下载](https://nlpr.ia.ac.cn/databases/handwriting/Download.html)与[使用条件](https://nlpr.ia.ac.cn/databases/handwriting/Application_form.html) | 学术研究条件由原权利人规定，不再分发源文件和裁图 |
| HME100K | [好未来平台](https://ai.100tal.com/dataset) | 原获取流程及已接受协议适用；本地登记为内部研究、禁止再分发，相关内容不上传 |
| ExamOCRBench | [官方数据卡](https://huggingface.co/datasets/jiangxuhao/ExamOCRBench) | 数据卡标注other，未取得明确完整再分发授权，不上传 |

这些链接是来源入口，不是本发行数据镜像，也不保证未来版本与本地已冻结版本完全一致。自行下载上游原包还不足以重建项目的全部选样、裁图与修订参考；原版运行身份以`benchmarks.json`的完整包清单SHA为准。需要完整标准包时须先获得相关权利和匹配的版本；不能把不同版本文件拼到一起通过检查。

## 便携依赖索引

公开转录的祖先证明、原始标注和源图/分片必须作为单独授权的本地对象接入。索引保存在仓库外，例如`licensed-dependencies/index.json`：

```json
{
  "schema": "ekb-licensed-dependency-index-v1",
  "files": [
    {
      "path": "objects/<完整SHA256>",
      "sha256": "<完整64位小写SHA256>",
      "aliases": ["<原证明里记录的路径字符串>"]
    }
  ]
}
```

示例中的尖括号是需替换的说明，不是可执行记录。对象路径相对索引目录，必须为本地普通文件，禁止绝对路径、父目录跳转、符号链接/重解析点、重复冲突映射。`aliases`仅作为历史字符串键，绝不直接打开其中的个人路径。按预期SHA选择的文件仍须通过继承的来源与内容绑定；读取和最终核验都检查真实字节，不能用同名文件替代。

`ekb_bench.py run ... --dependencies /path/to/index.json`将索引传给公开运行器；直接调用vendor工具时可设置环境变量`EKB_BENCH_DEPENDENCIES`。索引、对象和所有运行输出均不得提交Git。该方式保留祖先合同、来源准入、几何和原有完整成员校验；缺项明确失败，不用减少分母绕过错误。

代码及协议不会提供能够绕过上游登录、协议或授权的数据下载器。公开机制自测只使用测试程序创建的原创字节，不能替代完整真实评测。
