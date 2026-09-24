# 数据快照与复现

## 来源与范围

本项目只使用 [纽约市出租车与礼车委员会（NYC TLC）官方 Trip Record Data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) 中 **2025 年 1 月、2 月 Yellow Taxi** 的 Parquet 文件，以及该页的 `taxi_zone_lookup.csv`。字段含义以 [Yellow Taxi 数据字典](https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_yellow.pdf) 为准。TLC 明确说明原始记录由获授权技术服务商提供，TLC 不保证准确性，因此分析结论须说明数据质量与覆盖范围。

`source_manifest.json` 固定了三个官方下载 URL、字节数、SHA-256 和快照标识。`raw/` 与 `nyc_taxi.duckdb` 是可重新生成的大文件，不应提交 Git。项目的金标数字以该快照和 `prepare_data.py` 的 v1 清洗规则为准。若官方替换了文件，下载会因哈希不匹配而停止；不能悄悄使用变化后的数据复现原评测。

## 准备

在项目根目录运行：

```bash
python -m pip install 'duckdb>=1.5,<2'
python scripts/prepare_data.py
python scripts/prepare_data.py --verify-only
```

约需下载 120 MB；构建后数据库约 208 MB。构建时将 DuckDB 内存限制为 2 GB、线程数设为 4，适合 16 GB Mac。若需要从已校验源文件重建，运行 `python scripts/prepare_data.py --force`。

## 数据模型和固定口径

- `zones(location_id, borough, zone, service_zone)`：官方区域映射，共 265 行。
- `trips`：保留每条合格行的上下车本地时间、时长、起终点区域 ID、人数、里程、车费、总费用、信用卡小费、支付方式、拥堵费及来源月份。
- `dataset_metadata`：快照号、原始清单哈希、清洗版本及行数。

原始月文件含少量相邻月份的上车记录。每个文件仅纳入**与文件名相同月份的上车时间**，避免跨月误计。然后仅保留时长 1–240 分钟、里程 0.1–100 英里、起点和终点均在官方区域映射中的记录。按此规则，1 月为 3,356,067 行、2 月为 3,443,673 行，合计 **6,799,740 行**。时间字段没有时区信息；本项目将其解释为纽约本地民用时间。两个月均不跨夏令时转换日。

清洗不会改写或填补金额与人数：因此做金额分析时还应在 SQL 中明确排除 `NULL`、负金额或异常值；小费分析只能代表**记录的信用卡小费**，官方字典说明现金小费不在 `tip_amount` 中。`trip_count` 表示行程条数，不等于独立乘客数；本数据没有乘客身份标识，不能计算独立乘客或追踪个人。

## 安全使用边界

应用运行时使用只读连接访问已物化数据库，并在连接层禁用外部文件/网络访问；**不要向 Agent 暴露原始 Parquet 路径或 DuckDB `read_parquet` 等任意文件读取能力**。DuckDB 官方把不可信 SQL 视为具有程序执行能力，单靠 `SELECT` 字符串检查不足以建立安全边界，参见 [DuckDB 安全指南](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview)。

## 快速检查

```bash
python - <<'PY'
import duckdb
con = duckdb.connect('data/nyc_taxi.duckdb', read_only=True)
print(con.execute('SELECT source_month, count(*) FROM trips GROUP BY 1 ORDER BY 1').fetchall())
print(con.execute('SELECT count(*) FROM zones').fetchone())
PY
```

期望输出分别是 `[('2025-01', 3356067), ('2025-02', 3443673)]` 和 `(265,)`。
