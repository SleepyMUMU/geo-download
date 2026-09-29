# Global Mapper 本地批量试验

`gm_batch.py` 对原任务范围的 26 个行政区使用同一个 Wayback 58924、z16 数据源。本次新和县已单独导出对照文件，批量默认处理其余 25 个县。它不上传夸克，也不改动原任务已验收的 GeoTIFF。

每县依次完成：按 `xinjiang_batch.py` 的 `SOURCE_FILE_CODES` 选行政区；校验已有 XYZ 瓦片并只补缺；将原始 PNG 瓦片封装为临时 MBTiles；用 Global Mapper v26 导出按县界裁切的 EPSG:326xx、5×5 米、单波段 256 色 GeoTIFF；全图回读、核对规格、计算 SHA-256；成功后删除临时 MBTiles。默认命令在整批成功后会清理瓦片；**当前备份任务必须加 `--keep-tiles`，`run_gm_batch.ps1` 已使用此参数**。失败地区保留日志、临时包和错误记录，并继续其余地区。

GM 导出参数取已验证的新和县默认参数：`PALETTE=OPTIMIZED`、`SAMPLING_METHOD=DEFAULT`、`COMPRESSION=PACKBITS`，不启用透明背景、内部瓦片分块或金字塔。此前新和县实测表明，这些存储选项与旧 GM 输出的全部 496,719,806 个像素索引及 256 色表完全相同，只影响文件组织和大小。

输出目录：`jobs/gm-batch-wayback58924-z16/output/`。命名为 `行政代码_名称_Wayback58924_z16_GM_8bit_5m_UTM分带N_default.tif`。每县状态、边界、GM 脚本和日志保存在同目录下的行政代码子目录；总状态为 `summary.json`。已有成功成果按 SHA-256 核对后跳过，不覆盖。

使用 `sat` 环境直接运行：

```powershell
C:\Users\81052\anaconda3\envs\sat\python.exe gm_batch.py --plan
C:\Users\81052\anaconda3\envs\sat\python.exe gm_batch.py --keep-tiles
```

先单县验证可加 `--only-code 652829`。脚本使用文件锁避免并发；机器或程序中断后重新运行同一命令即可续做，未完成的 MBTiles 可以续写。默认 16 个下载线程，可用 `--download-threads` 调整；`--keep-tiles` 可保留全部 XYZ 缓存。GM 导出本身一次只运行一个县。

`gm_status.json` 的 `complete` 表示自动规格、全图回读和本地 SHA 已通过；每县仍会保留 GeoTIFF 供人工检查画质。脚本不会把自动检查冒充人工视觉复核。

## 当前瓦片 7z 备份

源目录为 `tiles_v2/4d430470504d4d5926b5/16/`，由 `backup_gm_tiles.py` 按 x 列组成约 10 GiB 的独立 7z LZMA2 高压包，存放在 `jobs/gm-tile-backup/volumes/`。`jobs/gm-tile-backup/ledger.json` 记录每包列范围、PNG 数量、原始与压缩字节数、本地 SHA、以及官方夸克上传和 fresh listing 核验。已记录的列不会再重复打包。

GM 仍可能写入且末/若羌瓦片时，只允许打包已证明不受两县使用的 `x<47948`，且不要与 GM 导出同时压缩：

```powershell
C:\Users\81052\anaconda3\envs\sat\python.exe backup_gm_tiles.py --safe-until-x 47948
```

GM 全批完成并确认 `summary.json` 的 `selected=25`、`failed_codes=[]` 和 25 个 `gm_status.json` 均为 `complete` 后，才可不带安全界继续覆盖剩余 x 列：

```powershell
C:\Users\81052\anaconda3\envs\sat\python.exe backup_gm_tiles.py
```

每次只产一个独立包；运行结束才读 ledger 并用官方夸克 Skill 在 Agent 前台上传到 `config.yaml` 已配置的“新疆”目录。上传前后都重新列表，核准确文件名、字节数及 FID；失败先查云端与官方任务记录，再决定恢复上传。大文件云端下载回读限制不等于上传失败。ledger 用无 BOM UTF-8 保存。PNG 本身已压缩，高压收益有限；检查 D 盘空间，原始瓦片及 TIFF 保留，云端未核验不得删本地包。

**完成条件**是 GM 26 县（含独立新和）自动核验通过，且 ledger 覆盖全部当前 x 列、所有包均通过本地 7z 测试/SHA 和夸克 fresh listing；旧脚本版的 26/26 状态不能替代。旧 `shutdown_after_gm.ps1` 不含瓦片备份条件，当前已停用；只有全部完成且无作业时，才依用户授权正常关机。实时进程和异常处理见 [CURRENT_TASK.md](CURRENT_TASK.md)。
