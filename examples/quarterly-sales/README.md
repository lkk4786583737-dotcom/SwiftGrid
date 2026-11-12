# 季度销量演示案例

[项目首页](../../README.md) · [演示说明与视频](../../docs/DEMO_CASE_zh.md) · [数据来源与许可](SOURCE_AND_LICENSE.md)

四张源表：`OrderHeader`、`OrderDetail`、`Product`、`Subcategory`。目标按子类别输出 Q1–Q4 销量、全年合计、销量排名与占比。

## 在 Web 中复现

1. 在“逻辑建模”中上传 `learn/source.xlsx` 与 `learn/target.xlsx`，开始学习；必要时根据真实业务规则澄清。
2. 检查结果后保存逻辑。
3. 在“处理新表”中选择该逻辑，上传 `period-b/source.xlsx`，导出结果。
4. 再上传 `period-c/source.xlsx`，检查只有上半年时 Q3、Q4 是否补 0。
5. 查看报告、基础统计与图表，尝试更换坐标轴并导出图片。

模型输出存在变化，上述步骤不保证每次无需澄清就通过。**B、C 两期的 `expected.xlsx` 和 `reference.py` 只用于事后核对，不能作为模型输入或补充提示。**

## 不调用模型，核对已有产物

安装项目依赖后，在仓库根目录运行：

```bash
python examples/quarterly-sales/verify_results.py
```

脚本从四张原始表独立计算参考值，先核对随附标准文件，再核对视频中真实下载的 `recorded/` 输出。比较列序、行序、数据值（浮点绝对容差 `1e-6`），并检查季度合计、总销量、排名、占比及上半年缺失季度。

Sheet 名独立报告：当前实际输出为“结果”，参考为“Sheet1”。默认命令的数据检查通过并报告名称差异；以下严格命令会因这项已知问题返回非零：

```bash
python examples/quarterly-sales/verify_results.py --strict-sheet-names
```

它不检查 Excel 样式、公式、宏等，也不重新生成模型逻辑。`recorded/learned-logic.py` 是本次运行保存的脚本，保留用于检查，不保证处理所有未知异常。`reference.py` 独立于产品引擎，定义本案例业务规则。

## 文件分工

| 路径 | 内容 |
| --- | --- |
| `learn/` | 学习源表与目标样例 |
| `period-b/`、`period-c/` | 两个复用期的源表与事后标准结果 |
| `recorded/` | 视频实际导出、学习得到的代码、运行与图表检查摘要 |
| `reference.py` | 从源表计算标准结果的独立参考实现 |
| `verify_results.py` | 离线结果与业务不变量核对 |

三期是同一业务案例，不是三个独立领域的准确率测试。所有数据均为官方样例的整理版本，不包含银行客户原始数据。
