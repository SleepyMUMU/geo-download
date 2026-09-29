# 新疆影像任务：新对话接手卡

更新于 2026-09-29。**先以磁盘、进程和云端的实时状态为准**；本文是接手入口，不是完成证明。新对话可直接说：

> 继续 `D:\WorkSpace\AI\Geo Download` 的 GM 原生导出及瓦片 7z 夸克备份。先读 `AGENTS.md`、本文件、`GM_BATCH_README.md` 和 `config.yaml`；检查 GM/7z/上传进程、锁、D 盘、GM 各县 `gm_status.json`、批次 `summary.json` 和备份 `ledger.json`。原脚本版 26 县已完成，不要当成这次 GM 任务完成，不要重复导出、压缩或上传。

## 当前任务：GM 原生导出与瓦片备份

- **与下文历史任务区分**：原脚本版 26 县 TIFF 已完成本地和夸克核验。用户后来改用 Global Mapper v26 原生优化调色板重做同一 Shapefile 范围的 8-bit、5 m UTM TIFF，且明确要求 GM 版 **不上传**。GM 默认采样、`PALETTE=OPTIMIZED`、PackBits、无透明背景、内部分块或金字塔；不是旧 GDAL `average`/DEFLATE 输出。新和县 `652925` 已单独 GM 导出，其余 25 县由 `gm_batch.py` 处理。GM 版 `complete` 只表示规格、全图回读和 SHA 自动通过，不冒充逐县人工视觉验收。
- **原始瓦片备份**：用户另行授权把 Wayback 58924 z16 的 `tiles_v2/4d430470504d4d5926b5/16/` 用 7z LZMA2 高压分包上传到夸克“新疆”文件夹。`run_gm_batch.ps1` 已用 `--keep-tiles`，不能恢复默认的成功后清瓦片行为。`backup_gm_tiles.py` 每次约 10 GiB 独立包，先 `7z t` 和本地 SHA，再由官方 `quarkclouddrive` Skill 在 Agent 可识别前台上传，fresh browse 核准确文件名、字节数和 FID，并把两种 FID 记入 `jobs/gm-tile-backup/ledger.json`。ledger 必须为无 BOM 的 UTF-8。上传失败先重新列表，再恢复官方任务记录，禁止盲目重传。
- **实时快照（2026-09-29 晚间）**：GM 批处理 24/25 县 `complete`，加独立新和县为 25/26。若羌 `652824` 已通过 GM 自动规格、全图回读和 SHA；且末 `652825` 前次导出意外中断，16 字节损坏 TIFF 已隔离为 `.interrupted-20260929.tif`。旧批处理自然结束并报告该县失败后，已确认无旧进程，以 `--keep-tiles` 启动唯一续跑进程（启动时 PID 28064），复用已核验瓦片和约 52.4 GiB MBTiles 重做且末。**PID 与阶段随时会变，先实时核进程、状态和 `worker.lock`。**
- **备份快照**：`ledger.json` 前两包 `x47234-47464`、`x47465-47609` 均通过本地 `7z t`/SHA、官方夸克上传及 fresh listing，合计约 21.3 GB。第二包上传首试报服务器内部异常，经确认云端无完整文件后用官方 `upload resume --record-id ... --file-path ...` 成功恢复。原始瓦片与两包本地压缩文件仍保留。其余列未备份，不能声称全量完成。
- **资源与调度**：唯一 GM 批处理运行中不得再启动第二条；且末重做期间暂停新 7z 压缩/上传，避免资源争用。先前确认剩余且末、若羌的 z16 瓦片 x 范围均在 `47948` 及以上，因此仅在其导出安全间隙可对 `x<47948` 用 `--safe-until-x 47948` 提前打包。GM 全批 `summary.json` 为 `selected=25` 且 `failed_codes=[]`、25 个状态均 `complete` 后，才能不带安全范围打包余下列。每包独立压缩与云端核验，监控 D 盘；仅对应压缩包已云端核验且空间告急时可清理该本地压缩包，保留原始瓦片和 TIFF。
- **关机门槛**：旧 `shutdown_after_gm.ps1` 监视进程已停止，不能在瓦片备份前重启。现有每小时轻量监控 `gm-7z`；健康且无变化时静默。只有 GM 26 县（含独立新和）全部自动核验、`ledger.json` 覆盖全部当前瓦片 x 列且每包云端 fresh listing 核验、无 GM/7z/上传进程时，才先报告用户，再按授权延迟 60 秒正常关机并关闭监控。任何失败或未核验都不得关机。

下文记录 **原脚本版** 历史流程；其中旧 PID、等待状态、完成后清缓存或关机指令不适用于当前 GM/瓦片备份任务。

## 原脚本版历史约束

- 原始 Shapefile 范围 `19_23.shp` 到 `42_43.shp`，16 个文件、26 个允许地区；自动排除代码 `659001`～`659011`。合并文件按 `xinjiang_batch.py` 的 `SOURCE_FILE_CODES` 解析。
- Wayback 58924、z16、5×5 m、按县中心选 WGS84 UTM 北半球分带、`average` 重采样、单波段 Byte/256 色调色板、分块 BigTIFF/DEFLATE/内部掩膜/金字塔。使用 `sat` 环境，不改全局环境。
- `config.yaml` 当前为 GDAL 16 线程、1024 MiB warp 内存和缓存、GTI 拼接。两个地区下载、一个压制、一个上传；同一时刻只能有一个 `xinjiang_batch.py`。用户授权上传到夸克“新疆”目录和完成全部验收后正常关机。
- 使用官方 `C:\Users\81052\.codex\skills\quarkclouddrive\SKILL.md`，在 Agent 可识别的前台上传；核对云端重新列表的 FID、准确文件名、大小。大文件超过 50 MiB 不能回读，不代表上传失败。保留全部最终本地 TIFF、质量、视觉和回执文件。缓存只能在本地及云端均核验、且未完成地区不再需要时清理。

## 原脚本版历史记录（2026-09-26 至 2026-09-27）

### 2026-09-27 06:58：26 县全部验收完成

- 最后若羌 `652824` 本地质量、全部对照图目视、规格、SHA 与官方夸克上传均通过；上传回执已记录 FID、准确文件名和 7,070,813,611 字节大小。上传后又独立获取夸克“新疆”文件夹完整 fresh listing。
- `jobs/xinjiang-selection-bf377779e0/final_audit.json` 记录本次 26/26 核验：每县最终本地文件大小、`quality.json` 全图回读、`visual_review.json` 与全部 comparison、SHA 记录、上传回执以及 fresh listing 中唯一的 FID/准确名称/大小。状态表 26/26 已上传并核验、失败 0；无影像处理或上传进程，无 `pipeline.lock`。全部最终本地成果保留。
- 监控可停止。用户已授权完成后正常关机；先向用户报告，再延迟 60 秒发出非强制 Windows 关机命令。下文为处理历史。

### 2026-09-27 约 05:55：若羌上传中

- 若羌 `652824` 唯一处理 PID `39524` 正常完成并退出，锁已释放。7,070,813,611 字节最终 TIFF 的 SHA-256 为 `45d49ac237798a85ff5005fbe385e9b75def8aea875933cac4ece56fe5999bce`，重算一致；全图质量 MAE 2.918、P99 13、蓝青区 MAE 4.599，全图回读通过。单波段 Byte/256 色、5 m、UTM 32645、DEFLATE、内部掩膜及金字塔均核验。
- 实际查看全部 `comparison.png`、`comparison_2.png`、`comparison_3.png`，`visual_review.json` 已记 pass；官方夸克 fresh browse 确认新疆文件夹无该准确文件名。
- 官方 Skill 已在可识别 Agent 前台启动唯一上传：PTY session `93232`，启动时 Python PID `47408`、Quark node PID `47764`。上传过程中绝不重复上传；结束后检查 `upload_receipt.json` 并独立 fresh listing 核 FID、准确名和大小。全部 26 县验收后且无作业时才可关机。监控提示以实时进程为准。

### 2026-09-26 深夜若羌 GTI 修复

- 若羌处理 PID `17088` 在索引续写到 882301 条后意外退出；未上传，也没有合格最终 TIFF。原 `reference_rgb.tif` 只有 16 字节，已另存为 `reference_rgb.incomplete-16bytes.tif`。旧锁已在确认进程不存在后清理。
- 根因证据：中断续写的 GeoPackage 虽有 882301 条瓦片记录，但缺少空间 RTree 索引，`gpkg_contents` 范围只覆盖后续写入的部分瓦片；GTI 因而只显示错误的 77056×137985 区域。
- `imagery.build_gti` 现会重建缺失的空间索引、按完整瓦片清单修正范围与计数，并校验 GTI 实际尺寸。若羌完整索引原件已备份为 `mosaic.gti.gpkg.backup-pre-extent-fix`；现有 `mosaic.gti.gpkg` 已修复，RTree 882301 条、范围覆盖全部瓦片、GTI 332544×320769，首块瓦片可正常回读。25 项 `sat` 测试通过。后续以实时进程和状态为准。

### 2026-09-26 约 20:00 的最新增量

### 2026-09-26 20:52 的若羌恢复

- 唯一主线 PID `3344` 退出后，若羌 `652824` 失败：`UNIQUE constraint failed: tiles.fid`。根因是中断的 GeoPackage 实际保留 815000 个已提交瓦片索引，但 `gpkg_ogr_contents.feature_count` 缓存错误地为 0；原代码按 OGR 缓存计数从第 1 条重写。索引原件和备份均未删除。
- 已修改 `imagery.build_gti` 使用 SQLite 实际表行数、最小/最大 FID 和样本路径校验续写进度，完成后修正缓存计数。`sat` 全套 24 项测试通过，提交 `2131cbe` 已推送。
- 确认旧主线、上传及修复进程全退出且 `pipeline.lock` 不存在后，启动**唯一若羌处理进程 PID `17088`**，它复用项目 `download`/`process` 和主锁，但只处理影像、**不执行夸克上传**，故可隐藏后台持续运行。锁应为 `17088`，日志 `jobs/process_ruoqiang.stdout.log` 与 `.stderr.log`。若它仍运行只监控，不启动主线或第二处理进程。完成后必须逐项检查 quality、全部 comparison 并实际目视、visual_review、本地 SHA，然后在 Agent 前台用官方 Skill 上传并 fresh cloud listing 核 FID/准确名/大小；本地最终文件保留。只有全部26县完成且无作业时才可关机。

- **25/26 县**本地和夸克已核验：沙雅 `652924` 修复版全图 MAE 2.766、蓝青区 MAE 6.775，通过目视、规格与 SHA；官方 Skill 前台上传并 fresh listing 核 FID/准确名/大小。所有最终本地文件保留。
- 确认旧主线 PID `10228`、修复和上传进程全部退出后，已清陈旧 `pipeline.lock`，从原文件范围和 session 在 Agent 前台启动唯一主线 **PID `3344`**，锁写 `3344`。当前剩余若羌 `652824`，该县部分 GTI 已有 815000/882301 条可核对续写。主线还活着时只监控，绝不启动第二条。

- **24/26 县**本地和夸克已核验；库车 `652902` 修复后全图质量通过，实际查看全部对照图、重算 SHA 并由官方 Skill 前台上传，fresh listing 核验通过。温宿、拜城也已核验。
- 沙雅 `652924` 修复后质量通过：全图 MAE 2.766、P99 11、蓝青区 MAE 6.775；已实际查看全部对照图并验 TIFF 规格、大小、SHA，写 `visual_review.json` 为 pass。官方 Skill 前台上传仍在进行时不得重复上传或启动主线；以实时进程和 `upload_receipt.json` 为准。
- 若羌 `652824` 尚未压制完成。主线旧 PID `10228` 已退出，`pipeline.lock` 为陈旧锁。待沙雅上传进程结束并云端核验后，确认无任何主线/修复/上传进程，再清陈旧锁，按原范围和 session 续跑唯一主线；`mosaic.partial.gti.gpkg` 已有 815000/882301 条，代码会安全续写。
- 新版提交 `94c364f`（GTI 续写、稀有蓝青调色板）及 `5918c11`（稀有样本扩充）均已推送，`sat` 全套 24 项测试通过。以下较早记录仅供追溯，不可覆盖此处实时状态。

- 已有 **23/26 县**通过本地质量、实际视觉复核、SHA 和夸克 fresh listing：温宿 `652922`、拜城 `652926` 于 16:30–16:31 新增核验。两县均在官方 Skill 的 Agent 前台上传，并保留本地成果。
- 原主线 PID `10228` 约 16:17 意外退出；检查时未发现主线、修复或上传残留进程，`jobs/pipeline.lock` 中 `10228` 已陈旧。D 盘约 674 GiB 空闲。退出原因尚未证实，切勿因锁存在认定进程仍在运行。
- 若羌 `652824` 的 GTI 部分索引已提交 815,000/882,301 条，原件及 `mosaic.partial.gti.gpkg.backup-815000` 保留。新版 `imagery.build_gti` 可核对并续写部分索引；先确认旧进程不存在，再安全续跑。
- 库车 `652902`、沙雅 `652924` 的 RGB 参考影像、失败质量记录和对照图保留，未上传。两县全图 MAE/P99 虽通过，但稀有蓝青区域 MAE 分别约 25.04/23.55，超出 8 的门槛。训练样本中蓝青像元分别仅 48/11 个；已修改调色板预留条件并加入测试，须在真实 RGB 上复压、全图复核后才可上传，不能直接放行旧候选成果。
- 代码 `73ed70d` 已推送；本次 GTI 续写和稀有蓝青样本修正需在 `sat` 测试通过后单独提交推送。`HANDOFF.md` 历史进程信息失效。

- 监控自动化 `automation` 每小时运行；健康时静默。余下 3 县：若羌 `652824`、库车 `652902`、沙雅 `652924`。
- 和静 `652827` 旧版蓝色冰川/湖泊变橄榄绿的问题已修复。旧 TIFF 和 sidecar 留在 `old_before_blue_fix/`，旧云端文件移入“旧版影像归档”；新版沿用哈希核验的 RGB，输出 SHA `0820ce9d9ff387cd0b07565c259b812e2eaedab3adff7f43a727820d30211c66`，全图 MAE 3.157、蓝青区 MAE 5.045/P99 12，规格、全图回读、目视、上传及 fresh cloud listing 均通过。阿克苏缺失的视觉复核也已补齐并核验。
- 稀有蓝青色预留调色板颜色、蓝青区误差门槛和禁止未视觉复核上传的改动已在 `73ed70d` 提交推送。`git status` 另有无关的 `.quarkclouddrive/` 和 `.cpg` 未跟踪文件，勿误删、误提交。
- 最新已推送提交 `b8c3a87` 修正且末县明亮区域偏色；且末新版已通过本地质量、视觉、哈希及夸克重新列表核验。5 个小县并行成果已并入主任务并核验；不要重新下载或上传。旧 `HANDOFF.md` 中关于“且末压制中”的叙述已失效。

## 接下来按顺序做

1. 复查进程、`jobs/pipeline.lock`、磁盘、`status.md`，逐县核 `state.json`、`quality.json`、`comparison*.png`、`visual_review.json`、本地 SHA、`upload_receipt.json` 和云端 fresh listing。旧状态表的文字不能覆盖 sidecar 与目视证据。任何上传/压制仍在运行时只监控。
2. 用 `sat` 跑项目测试；只提交本次代码和交接文档，推送 GitHub，勿带入无关文件。
3. 先确认旧进程和上传均不存在，再检查锁、未完成中间文件，以原范围、原 session ID `1790129428-xj43bb`、`jobs/session-input.txt` 在 Agent 前台安全续跑。单县失败保留错误并继续；每县新成果都需实际看全部 `comparison*.png`、记录 `visual_review.json` 后才可上传。按安全条件清理缓存。
4. 全部 26 县本地规格、质量、视觉、SHA 与夸克 fresh listing 均核验，且无压制/上传在运行后，先报告用户，再按已有授权执行延迟 60 秒的 Windows 正常关机，并关闭监控。任何失败、未完成或未核验时不得关机。

`README.md` 说明参数和命令，`jobs/xinjiang-selection-bf377779e0/` 是实时任务证据。不要使用 `PROJECT_ARCHIVE_REPORT.md` 的旧结论。GM 官方只定义了 8-bit/256 色与优化调色板概念，并未公开完整选色算法；不能声称本程序与 GM 逐像素相同。
