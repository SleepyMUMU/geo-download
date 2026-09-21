# Geo Download v2

按用户指定的 ZIP 内文件识别城市，下载整座城市影像，输出 5 m UTM、GM 8-bit 调色板 GeoTIFF。

## 当前状态（2026-09-21）
本地流程通过 12 项自动测试及威海 36 瓦片实测。夸克按用户要求搁置，不启动上传。
尚未收到用户的实际任务压缩包；真实文件识别仍需收到后验证。尚未进行全市大规模性能验收。
旧 PROJECT_ARCHIVE_REPORT.md 是历史记录，不能作为当前操作指令。

## 新疆县级边界（已配置）

用户提供的 `新疆_下载.zip` 总表共有 107 个边界。按用户规则排除原顺序前 15 个和后 15 个；用户另列的福海县、哈巴河县、青河县、吉木乃县及 11 个自治区直辖县级市，恰好就是后 15 个。现有 77 个可选目标已固化为 `boundaries/xinjiang_available.gpkg`。

```text
python xinjiang.py list
python xinjiang.py plan --name 伊州区 --name 昌吉市
python xinjiang.py run jobs/xinjiang-<任务ID>/plan.json
```

可用名称或行政代码选择；不可选、错字或不唯一目标会直接停止。每个目标按其自身边界下载，并按边界中心选择 WGS84 UTM 分带。面积横跨 UTM 标准分带的超大县仍使用中心分带，5 m 指输出投影像元尺寸，边缘存在投影尺度变形。

## 环境与入口
使用已安装的 conda sat 环境。依赖：GDAL、geopandas、mercantile、shapely、pyyaml、requests、numpy、Pillow。
所有参数统一在 config.yaml；不再使用 config_process.yaml。
在项目目录激活环境后运行：

```text
python check_env.py
python pipeline.py inspect 输入.zip
python pipeline.py plan --archive 输入.zip --select "山东省/威海市.txt"
python pipeline.py run jobs/<任务ID>/plan.json
```

可重复传 --select，必须是 ZIP 内精确路径。文件名可识别城市、省份和行政代码；SHP、GeoJSON、单图层 GPKG、KML、GeoTIFF 可通过地理位置识别并核对属性。SHP 必须带齐 SHX/DBF/PRJ。未知、跨市、名称冲突均停止。ZIP 外的未知格式不保证支持，不执行压缩包内脚本。
文件只用于识别城市；下载范围为整座城市行政边界，不是输入文件的小范围。

直接指定城市也可：
```text
python pipeline.py plan --city 威海
python pipeline.py run jobs/<任务ID>/plan.json
```

run.py 保留单城市兼容入口。旧省级、下载、处理、上传脚本停用并明确报错，以免继续运行旧逻辑。run_pipeline.bat 转发 pipeline.py 参数，可设置 GEO_PYTHON 或使用 conda sat。

## 已确定参数
- 来源：原配置 Esri World Imagery Wayback，版本 58924；官方版本表对应 2025-09-25 发布。发布日期不是各处影像拍摄日期。
- 输入：z16、256×256 XYZ、EPSG:3857；保留原图解码后的 RGB 值，缓存为 PNG，按图源版本隔离。
- 输出：按城市中心选择 WGS84 UTM 分带，5×5 投影米像元。威海 EPSG:32651（51N）。这不是地面定位精度保证，也不代表原始影像实际分辨率一定为 5 m。
- 重采样：average，一次重投影后再量化。
- 色彩：GM 所指 8-bit Palette Image，单波段 Byte、256 色；整座城市共享抽样优化调色板，采用全局坐标一致的轻度有序抖动。不是 RGB 每通道 8 位。
- 压缩：DEFLATE 6、分块 BigTIFF、内部掩膜、NEAREST 金字塔。压缩无损，颜色量化有损。
- 不做自动 GCJ02 纠偏；打包边界的来源及实际定位精度未独立验收。

## 验收与恢复
缺瓦片、损坏瓦片、下载失败均停止；失败任务留有 state.json。缓存只复用可解码且尺寸正确的瓦片。
成果写入临时文件，验证像元、投影、掩膜、金字塔及逐块回读后再改为正式文件。
质量门槛：平均通道绝对误差 ≤6 DN、99% 通道绝对误差 ≤24 DN。这是工程筛查，不能替代视觉检查。
在 jobs/<任务ID>/<城市代码>/ 查看 quality.json、comparison*.png（左 RGB，右调色板）、reference_rgb.tif。
查看对照图后可记录检查：python pipeline.py review jobs/<任务ID>/plan.json --note "实际观察记录"。
不自动删除原始瓦片、RGB 参考或成果。不凭文件存在跳过，须核对任务参数、边界及成果哈希。
多个进程不能同时运行，异常退出遗留 pipeline.lock 时先核实进程已停止，再人工处理旧锁。

## 已完成验证
12 项测试覆盖城市精确匹配、压缩包选择与越界路径、空间识别和名称冲突、图源缓存隔离、损坏及缺失瓦片、下载失败、调色板跨块一致、GeoTIFF 回读和上传失败状态。
真实威海小样：36 块瓦片，UTM 51N、5×5 m；平均通道误差 2.98/255、RMSE 3.95、P99 11/255。对照图未见明显新增色带或接缝。不能据此保证任意城市、任意图源都通过；每次任务仍须检查。

## 夸克（搁置）
旧工具已换为官方 CLI 适配草案。实测 64 MiB 上传返回成功；下载回读被服务端 50 MiB 限制阻止。云端独立核验未在本次收尾中确认完成。不要执行 upload，也不要删本地文件。用户恢复该需求后再验收。
