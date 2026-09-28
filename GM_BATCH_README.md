# Global Mapper 本地批量试验

`gm_batch.py` 对原任务范围的 26 个行政区使用同一个 Wayback 58924、z16 数据源。本次新和县已单独导出对照文件，批量默认处理其余 25 个县。它不上传夸克，也不改动原任务已验收的 GeoTIFF。

每县依次完成：按 `xinjiang_batch.py` 的 `SOURCE_FILE_CODES` 选行政区；校验已有 XYZ 瓦片并只补缺；将原始 PNG 瓦片封装为临时 MBTiles；用 Global Mapper v26 导出按县界裁切的 EPSG:326xx、5×5 米、单波段 256 色 GeoTIFF；全图回读、核对规格、计算 SHA-256；成功后删除临时 MBTiles。整批成功后清理这批已用完的 XYZ 瓦片。失败地区保留日志、临时包和错误记录，并继续其余地区。

GM 导出参数取已验证的新和县默认参数：`PALETTE=OPTIMIZED`、`SAMPLING_METHOD=DEFAULT`、`COMPRESSION=PACKBITS`，不启用透明背景、内部瓦片分块或金字塔。此前新和县实测表明，这些存储选项与旧 GM 输出的全部 496,719,806 个像素索引及 256 色表完全相同，只影响文件组织和大小。

输出目录：`jobs/gm-batch-wayback58924-z16/output/`。命名为 `行政代码_名称_Wayback58924_z16_GM_8bit_5m_UTM分带N_default.tif`。每县状态、边界、GM 脚本和日志保存在同目录下的行政代码子目录；总状态为 `summary.json`。已有成功成果按 SHA-256 核对后跳过，不覆盖。

使用 `sat` 环境直接运行：

```powershell
C:\Users\81052\anaconda3\envs\sat\python.exe gm_batch.py --plan
C:\Users\81052\anaconda3\envs\sat\python.exe gm_batch.py
```

先单县验证可加 `--only-code 652829`。脚本使用文件锁避免并发；机器或程序中断后重新运行同一命令即可续做，未完成的 MBTiles 可以续写。默认 16 个下载线程，可用 `--download-threads` 调整；`--keep-tiles` 可保留全部 XYZ 缓存。GM 导出本身一次只运行一个县。

`gm_status.json` 的 `complete` 表示自动规格、全图回读和本地 SHA 已通过；每县仍会保留 GeoTIFF 供人工检查画质。脚本不会把自动检查冒充人工视觉复核。
