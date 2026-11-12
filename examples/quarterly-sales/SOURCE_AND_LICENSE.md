# 数据来源与处理说明

- 来源：[Microsoft SQL Server Samples / AdventureWorks](https://github.com/microsoft/sql-server-samples/tree/master/samples/databases/adventure-works)。
- 下载来源：[AdventureWorks OLTP install script](https://github.com/Microsoft/sql-server-samples/releases/download/adventureworks/AdventureWorks-oltp-install-script.zip)。
- 原始下载记录日期：2026-08-24。
- 许可：MIT；[随附完整 Microsoft 许可](LICENSE-Microsoft.txt)，[上游许可](https://github.com/microsoft/sql-server-samples/blob/master/license.txt)。
- 隐私：官方示例数据，非真实银行客户数据；不代表 Microsoft 对本项目的认可。

## 本案例的整理方式

基于本项目已整理的 AdventureWorks 数据，按订单日期拆成学习期、下一年与仅上半年三期；工作簿展示年份为 2023、2024、2025。保留所需列，每个工作簿包含 `OrderHeader`、`OrderDetail`、`Product`、`Subcategory` 四张独立表。商品和子类别是各期共同的主数据。

源表未预先关联；三次关联由参考计算与模型生成的处理脚本各自完成。该案例不是完整 AdventureWorks 数据库，也不将展示年份解释为真实商业事件年份。

`reference.py` 根据源表生成目标规则：按商品子类别和订单季度汇总销量，补齐四季度列，计算全年合计、降序竞争排名与四位小数占比，最后按排名与子类别排序。B、C 两期参考结果仅用于事后核对，未作为视频中学习或复用的输入。

`recorded/` 中的 Excel 为演示运行实际下载产物，不是从标准文件复制而成。数据内容核对通过，但 Sheet 名与参考文件不同，见 [核对方法](README.md)。
