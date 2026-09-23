# Geo Download：新疆卫星影像下载流水线

本项目根据用户指定的**原始 Shapefile 文件范围**下载行政区卫星影像，输出 5 m UTM、Global Mapper 风格 8-bit 调色板 GeoTIFF，并可上传到夸克网盘。

> Agent 接手后先读 `AGENTS.md`、本文件和 `config.yaml`。`PROJECT_ARCHIVE_REPORT.md` 是旧版历史材料，不能作为当前操作指令。

## Agent 五分钟接手流程

1. 获取仓库并进入目录：

   ```powershell
   git clone git@github.com:SleepyMUMU/geo-download.git
   cd geo-download
   git checkout master
   ```

2. 使用项目要求的 `sat` Conda 环境，不修改全局环境：

   ```powershell
   conda activate sat
   python check_env.py
   python -m unittest discover -s tests -v
   ```

3. 向用户确认四件事：

   - 是否继续使用当前已核验的 `新疆_下载.zip` 文件映射；若换了 ZIP，提供新包路径并重新核对；
   - 起始 Shapefile 文件名；
   - 结束 Shapefile 文件名；
   - 是否上传夸克，以及目标文件夹。

4. 对照下方“当前文件区域”确认范围。这里的“区域”是**文件清单上的连续范围**，不是城市名称或行政区序号。

5. 检查 `config.yaml` 的图源、代理和夸克目录 FID，然后在前台运行 `xinjiang_batch.py`。不要用脱离 Agent 环境的隐藏后台进程执行夸克上传。

## 当前文件区域

当前默认选择：从 `19_23.shp` 到 `42_43.shp`，共 16 个原始 Shapefile、26 个允许下载的行政区。

| 顺序 | 原始文件 | 文件内地区 | 自动排除 |
|---:|---|---|---|
| 1 | `19_23.shp` | 阜康市、吉木萨尔县 | — |
| 2 | `20_21.shp` | 呼图壁县、玛纳斯县 | — |
| 3 | `22_24.shp` | 奇台县、木垒哈萨克自治县 | — |
| 4 | `25_26_28.shp` | 博乐市、阿拉山口市、温泉县 | — |
| 5 | `102_27.shp` | 精河县、双河市 | 双河市 |
| 6 | `29_101.shp` | 库尔勒市、铁门关市 | 铁门关市 |
| 7 | `30.shp` | 轮台县 | — |
| 8 | `31.shp` | 尉犁县 | — |
| 9 | `32.shp` | 若羌县 | — |
| 10 | `33.shp` | 且末县 | — |
| 11 | `34_35.shp` | 焉耆回族自治县、和静县 | — |
| 12 | `36_37.shp` | 和硕县、博湖县 | — |
| 13 | `38_40_45_97.shp` | 阿克苏市、温宿县、阿瓦提县、阿拉尔市 | 阿拉尔市 |
| 14 | `39.shp` | 库车市 | — |
| 15 | `41.shp` | 沙雅县 | — |
| 16 | `42_43.shp` | 新和县、拜城县 | — |

自治区直辖县级市代码 `659001`～`659011` 会自动排除，即使它与允许地区位于同一个合并文件中。

### 选择文件范围

最直接的方法是修改 `xinjiang_batch.py` 顶部：

```python
FILE_RANGE_START = '19_23.shp'
FILE_RANGE_END = '42_43.shp'
```

也可以在运行时覆盖：

```powershell
python xinjiang_batch.py `
  --start-file 30.shp `
  --end-file 36_37.shp `
  --downloaders 2 `
  --session-id "本次任务唯一ID" `
  --session-input-file "jobs/session-input.txt"
```

若只选不连续文件，重复使用 `--source-file`；它会覆盖起止范围：

```powershell
python xinjiang_batch.py `
  --source-file 30.shp `
  --source-file 34_35.shp `
  --session-id "本次任务唯一ID" `
  --session-input-file "jobs/session-input.txt"
```

新增原始文件时，必须同时在 `SOURCE_FILE_ORDER` 和 `SOURCE_FILE_CODES` 中登记。合并文件要列出内部所有行政代码，不能仅根据文件名猜测。

## 配置下载和影像参数

统一修改 `config.yaml`：

```yaml
custom_url: https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/58924/{z}/{y}/{x}
zoom: 16
resolution: 5
color_mode: palette8
resampling: average
proxy: http://127.0.0.1:7897
max_threads: 8
processing_threads: 8
warp_memory_mb: 512
gdal_cache_mb: 512
```

当前输出参数：

- 图源：Esri World Imagery Wayback 58924，版本发布日期 2025-09-25；发布日期不等于各地影像拍摄日期。
- 输入：z16、256×256 XYZ、EPSG:3857。
- 输出：按行政区中心确定 WGS84 UTM 北半球分带，5×5 m 像元。
- 色彩：单波段 Byte、256 色共享调色板，带轻度有序抖动；这是 GM 所指的 8-bit Palette Image，不是 RGB 每通道 8 位。
- 重采样：`average`，重投影后再量化。
- 压缩：DEFLATE、分块 BigTIFF、内部掩膜和金字塔。
- GDAL 压制使用 8 个处理线程、512 MiB 重投影内存和 512 MiB 块缓存；这些设置只对新启动的流水线进程生效。正在处理的地区不会在运行中切换线程数。

`resolution: 5` 是输出投影像元尺寸，不代表原始影像定位精度或真实空间分辨率必然达到 5 m。调色板量化有损，DEFLATE 压缩本身无损。

## 配置夸克网盘

项目仅使用官方 `quarkclouddrive` Skill。Agent 必须先读取其 `SKILL.md` 并按 Skill 完成登录、建目录和查询，不能恢复旧上传脚本或伪造成功结果。

`config.yaml` 中的配置示例：

```yaml
quark:
  skill_dir: ~/.codex/skills/quarkclouddrive
  bash: C:/Program Files/Git/bin/bash.exe
  timeout_seconds: 21600
  parent_fid: d2466a17d22d4d1eb29e23b56c256cb4
  folder_label: 夸克网盘默认位置/新疆
```

配置步骤：

1. 通过官方 Skill 登录夸克。
2. 浏览或创建用户指定的目标文件夹。
3. 将该文件夹真实 `fid` 写入 `parent_fid`；`folder_label` 只供人阅读，不能代替 FID。
4. 把用户本次明确授权下载/上传的**原始消息原文**保存到 `jobs/session-input.txt`。`jobs/` 已被 Git 忽略，不要提交用户消息或凭据。
5. 为整次运行使用同一个唯一 `--session-id`。
6. 在当前 Agent 的前台命令中运行。官方工具可能拒绝无法识别 Agent 身份的后台子进程。

大文件上传成功后，以云端重新列表得到的 FID、文件名和大小进行核验。服务端下载回读目前存在约 50 MiB 限制，因此不能把“大文件无法回读”误判为上传失败，也不能只看终端日志就删除本地文件。

如果暂时不具备官方 Skill、有效登录或目标 FID，应先完成本地下载与压制并保留成果，不得声称已上传。

## 运行、状态与续跑

运行前先创建会话输入文件：

```powershell
New-Item -ItemType Directory -Force jobs | Out-Null
Set-Content -Encoding UTF8 jobs/session-input.txt "粘贴用户本次授权任务的原始消息"
```

按默认文件范围运行：

```powershell
python xinjiang_batch.py `
  --downloaders 2 `
  --session-id "本次任务唯一ID" `
  --session-input-file "jobs/session-input.txt"
```

并发结构为两个地区下载、一个压制消费者、一个上传线程。不要同时启动第二个流水线进程。

运行状态保存在：

```text
jobs/xinjiang-selection-<选择哈希>/status.md
jobs/xinjiang-selection-<选择哈希>/status.csv
jobs/xinjiang-selection-<选择哈希>/status.json
```

状态表包含源文件、城市、下载状态、压制状态和上传状态。每次状态变化都会原子更新。异常退出后先确认没有残留 Python 流水线进程，再处理 `pipeline.lock`。重新运行同一选择时，仅复用哈希和文件检查都通过的已有成品。

## 质量验收

每个行政区的任务目录包含：

- `state.json`：处理状态和成果哈希；
- `quality.json`：全图回读质量指标；
- `comparison*.png`：RGB 参考与调色板成果对照；
- `reference_rgb.tif`：量化前参考影像；
- `upload_receipt.json`：夸克上传及核验信息。

自动门槛为平均通道绝对误差不超过 6 DN、P99 通道绝对误差不超过 24 DN。通过数值检查后仍需查看对照图，确认没有新增色带、瓦片缺失、黑边或明显色偏。

下载失败、缺瓦片、损坏瓦片、投影错误或质量不合格均不得标记为完成。经用户最新指示，已完成且通过本地哈希、质量、视觉复核和夸克云端回执核验的地区，可自动清理其参考影像、拼接 VRT 和不再被任何未完成地区使用的缓存瓦片。本地 GeoTIFF、质量记录、对照图和上传回执始终保留。

长时间运行的既有流水线可另开一个前台终端启动缓存清理监控（它不是第二条下载流水线）：

```powershell
python cleanup_cache.py --watch
```

先用 `python cleanup_cache.py --dry-run` 查看可回收量。清理依据是当前文件范围的行政区映射；若瓦片落在任一未完成地区的边界框内，即使它已经用于其他地区也继续保留。云端未核验或视觉复核记录缺失时，不删除该地区的缓存。

## 其他入口

通用 ZIP 文件识别流程：

```powershell
python pipeline.py inspect "输入.zip"
python pipeline.py plan --archive "输入.zip" --select "ZIP内精确路径"
python pipeline.py run "jobs/<任务ID>/plan.json"
```

直接按行政区名称建立单任务：

```powershell
python xinjiang.py list
python xinjiang.py plan --name 昌吉市
python xinjiang.py run "jobs/xinjiang-<任务ID>/plan.json"
```

这些入口适合单地区或通用压缩包；新疆批量文件范围任务统一使用 `xinjiang_batch.py`。
