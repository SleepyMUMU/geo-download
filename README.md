# 🛰️ Geo Download — 高精度卫星影像自动化处理与备份流水线

> 一个基于 Python + GDAL 的全自动卫星影像瓦片下载、高精度重采样压制与夸克网盘备份流水线，支持按城市或整省批量处理。

---

## ✨ 功能特性

- **智能空间裁剪**：通过 `mercantile` + `shapely` 计算与城市边界精确相交的瓦片，避免处理无关数据
- **超大规模优化**：可从 80 万+瓦片中按城市边界精确过滤（VRT 文件大小由 1.28 GB 骤降至 86 MB）
- **省级异步双引擎**：生产者-消费者并发模型，下载与压制并行，GPU 级别效率
- **坐标系纠偏**：内置 GCJ-02 → WGS-84 高精度迭代逼近算法
- **高精度输出**：5 米/像素分辨率，DEFLATE 极限压缩，带金字塔的 GeoTIFF
- **自动云端备份**：压制完成后自动通过 `kuake` 工具将成果上传至夸克网盘

---

## 📁 项目结构

```
Geo Download/
├── run.py                      # 单城市流水线入口
├── run_province.py             # 省份批量异步流水线入口
├── run_pipeline.bat            # 一键运行批处理脚本（Windows）
├── download_tiles.py           # 瓦片下载模块
├── process_tiles.py            # 影像拼合、重采样与压制模块
├── upload_to_quark.py          # 夸克网盘自动备份模块
├── convert_gcj02_to_wgs84.py   # GCJ-02 坐标系纠偏工具
├── check_env.py                # 运行环境依赖检测脚本
├── config.yaml                 # 下载配置文件（图源、Zoom 级别、代理等）
├── config_process.yaml         # 处理配置文件（分辨率、压缩算法、SRS 等）
├── city_detailed/              # 高精度市级行政边界 Shapefile (WGS-84)
├── sheng_detailed/             # 省级行政边界 Shapefile (WGS-84)
├── tiles/                      # [运行时生成] 瓦片缓存目录（.gitignore 忽略）
├── output/                     # [运行时生成] 压制成果目录（.gitignore 忽略）
└── logs/                       # [运行时生成] 异步任务日志（.gitignore 忽略）
```

---

## 🚀 快速上手

### 1. 环境准备

```bash
conda create -n sat python=3.10
conda activate sat
conda install -c conda-forge gdal geopandas mercantile shapely pyyaml requests
```

### 2. 配置文件修改

编辑 `config.yaml` 设置瓦片图源与代理：

```yaml
tile_source: "custom"
custom_url: "https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/58924/{z}/{y}/{x}"
zoom: 16
proxy: "http://127.0.0.1:7897"  # 按需修改
```

### 3. 一键运行

**双击批处理脚本（推荐）：**

```
run_pipeline.bat
```

**或通过命令行传参：**

```bash
# 单城市模式
run_pipeline.bat 威海

# 省份批量模式（辽宁省 14 个地级市）
run_pipeline.bat 辽宁
```

**或直接调用 Python：**

```bash
conda activate sat

# 单城市
python run.py 威海市

# 整省
python run_province.py 辽宁
```

---

## ⚙️ 处理参数说明

| 参数 | 说明 | 默认值 |
|:---|:---|:---|
| `zoom` | 瓦片 Zoom 级别 | `16` |
| `resampling_method` | 重采样算法 | `cubic` |
| `resolution` | 目标分辨率 (米/像素) | `5.0` |
| `output_srs` | 输出坐标系 | `EPSG:4326` |
| `max_threads` | 并发下载线程数 | `8` |

---

## 📊 辽宁省实战数据参考

| 城市 | 瓦片数 | 文件大小 |
|:---|:---|:---|
| 大连市 | 73,115 | 2.30 GB |
| 丹东市 | 95,960 | 2.39 GB |
| 朝阳市 | 129,510 | 3.26 GB |
| 抚顺市 | 70,899 | 1.81 GB |
| 沈阳市 | 81,993 | 2.10 GB |
| 铁岭市 | 65,399 | 1.98 GB |
| *全省合计* | *898,700+* | *~23.6 GB* |

---

## 🔑 夸克网盘备份说明

`upload_to_quark.py` 通过 [`kuake`](https://github.com/用户) CLI 工具实现自动备份。使用前需配置夸克 Cookie：

```
C:\Users\<用户名>\.gemini\config\skills\kuake_skill\cookie.txt
```

---

## 📄 License

MIT
