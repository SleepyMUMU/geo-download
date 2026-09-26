# 新疆影像任务：新对话接手卡

更新于 2026-09-26。**先以磁盘、进程和云端的实时状态为准**；本文是接手入口，不是完成证明。新对话可直接说：

> 继续 `D:\WorkSpace\AI\Geo Download` 的新疆影像任务。先依次读 `AGENTS.md`、`README.md`、`HANDOFF.md`、`config.yaml` 和 `jobs/xinjiang-selection-bf377779e0/status.md`，再读 `CURRENT_TASK.md`，检查进程、锁、磁盘及各县 sidecar，从当前暂停点安全续做。不要重复启动流水线、下载或上传。

## 不变的任务约束

- 原始 Shapefile 范围 `19_23.shp` 到 `42_43.shp`，16 个文件、26 个允许地区；自动排除代码 `659001`～`659011`。合并文件按 `xinjiang_batch.py` 的 `SOURCE_FILE_CODES` 解析。
- Wayback 58924、z16、5×5 m、按县中心选 WGS84 UTM 北半球分带、`average` 重采样、单波段 Byte/256 色调色板、分块 BigTIFF/DEFLATE/内部掩膜/金字塔。使用 `sat` 环境，不改全局环境。
- `config.yaml` 当前为 GDAL 16 线程、1024 MiB warp 内存和缓存、GTI 拼接。两个地区下载、一个压制、一个上传；同一时刻只能有一个 `xinjiang_batch.py`。用户授权上传到夸克“新疆”目录和完成全部验收后正常关机。
- 使用官方 `C:\Users\81052\.codex\skills\quarkclouddrive\SKILL.md`，在 Agent 可识别的前台上传；核对云端重新列表的 FID、准确文件名、大小。大文件超过 50 MiB 不能回读，不代表上传失败。保留全部最终本地 TIFF、质量、视觉和回执文件。缓存只能在本地及云端均核验、且未完成地区不再需要时清理。

## 当前暂停点

- 2026-09-26 核对后，原主流水线没有运行；`jobs/pipeline.lock` 仍写旧 PID `12832`，清锁和启动前必须重新核进程。旧状态表的“温宿县压制中”已更正为等待安全续跑。历史监控自动化曾暂停，恢复时检查其实际状态。
- **21/26 县**已实际查看对照图、全图质量通过、本地 TIFF SHA 复核、上传回执通过，并与 2026-09-26 的夸克 fresh 完整列表逐一核对唯一 FID、准确文件名、大小；状态表已刷新。余下 5 县：若羌 `652824`、温宿 `652922`、库车 `652902`、沙雅 `652924`、拜城 `652926`。温宿旧进程留下约 1.1 GB 的未完成 RGB 中间件，不能未经哈希和完整性验证直接复用。
- 和静 `652827` 旧版蓝色冰川/湖泊变橄榄绿的问题已修复。旧 TIFF 和 sidecar 留在 `old_before_blue_fix/`，旧云端文件移入“旧版影像归档”；新版沿用哈希核验的 RGB，输出 SHA `0820ce9d9ff387cd0b07565c259b812e2eaedab3adff7f43a727820d30211c66`，全图 MAE 3.157、蓝青区 MAE 5.045/P99 12，规格、全图回读、目视、上传及 fresh cloud listing 均通过。阿克苏缺失的视觉复核也已补齐并核验。
- `imagery.py`、`xinjiang_batch.py`、`tests/test_pipeline.py` 有已测试但尚待提交的改动：稀有蓝青色预留调色板颜色，新增蓝青区误差门槛，禁止未视觉复核即上传。`sat` 的 21 项测试通过。`git status` 另有无关的 `.quarkclouddrive/` 和 `.cpg` 未跟踪文件，勿误删、误提交。
- 最新已推送提交 `b8c3a87` 修正且末县明亮区域偏色；且末新版已通过本地质量、视觉、哈希及夸克重新列表核验。5 个小县并行成果已并入主任务并核验；不要重新下载或上传。旧 `HANDOFF.md` 中关于“且末压制中”的叙述已失效。

## 接下来按顺序做

1. 复查进程、`jobs/pipeline.lock`、磁盘、`status.md`，逐县核 `state.json`、`quality.json`、`comparison*.png`、`visual_review.json`、本地 SHA、`upload_receipt.json` 和云端 fresh listing。旧状态表的文字不能覆盖 sidecar 与目视证据。任何上传/压制仍在运行时只监控。
2. 用 `sat` 再跑项目测试；只提交上述改动及交接文档，推送 GitHub，勿带入无关文件。
3. 确认无旧进程/上传后处理陈旧锁，以原范围、原 session ID `1790129428-xj43bb`、`jobs/session-input.txt` 在 Agent 前台只启一条主流水线，复用已核验成果，继续余下 5 县。单县失败保留错误并继续；每县新成果都需实际看全部 `comparison*.png`、记录 `visual_review.json` 后才可上传。按安全条件清理缓存。
4. 全部 26 县本地规格、质量、视觉、SHA 与夸克 fresh listing 均核验，且无压制/上传在运行后，先报告用户，再按已有授权执行延迟 60 秒的 Windows 正常关机，并关闭监控。任何失败、未完成或未核验时不得关机。

`README.md` 说明参数和命令，`jobs/xinjiang-selection-bf377779e0/` 是实时任务证据。不要使用 `PROJECT_ARCHIVE_REPORT.md` 的旧结论。GM 官方只定义了 8-bit/256 色与优化调色板概念，并未公开完整选色算法；不能声称本程序与 GM 逐像素相同。
