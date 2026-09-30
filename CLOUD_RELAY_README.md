# 轻量瓦片流转节点（初版）

只在 `codex-cloud-demo` 分支使用。固定 Wayback 58924/z16，保留图源返回的 JPEG/PNG 原始字节。
不拼接、不重投影、不做 GM 处理，不读取生产缓存，不使用 localhost 代理。
小样实测及限制见 [CLOUD_RELAY_RESULTS.md](CLOUD_RELAY_RESULTS.md)。

## 环境与权限

运行只需要 Python 3.12、`relay-requirements.txt` 和 Linux `7z`。
在独立虚拟环境安装依赖，不改全局 Python：

```bash
git clone --branch codex-cloud-demo --single-branch https://github.com/SleepyMUMU/geo-download.git
cd geo-download
python3 -m venv .venv/relay
.venv/relay/bin/pip install -r relay-requirements.txt
```

通过云环境安装脚本提供 `7z`（常见包名为 p7zip-full 或 7zip），然后确认 `7z` 可执行。
允许 `wayback.maptiles.arcgis.com` 的 HTTPS 访问；下载自动使用环境的 HTTP(S)_PROXY。
没有固定字节速率限制，默认 8 并发，支持 1–32；尊重服务端 429/Retry-After。
较长 Retry-After 会暂停并保留状态，而不是立即重复请求。

只有生成区域清单的 `plan` 命令需要仓库已有的 `sat` GIS 环境（GeoPandas/GDAL/mercantile 等）。
可以在已有 GIS 工作节点生成计划，再把 `plan.json` 送给轻量节点执行；计划不含登录凭据。
在真实 Plus 8 GiB 节点不要安装完整研究 GIS 环境后再假定剩余空间足够。

## 小样计划与本地链路验证

在 `sat` 环境生成新和县已授权范围内 128 块瓦片的小样：

```bash
python cloud_relay.py plan --code 652925 --tile-limit 128
```

控制台给出 `jobs/cloud-relay/<plan-id>/plan.json` 的绝对路径。
默认小样从县界内部代表点向外选取相交瓦片；不是整个县。
代码必须属于仓库当前原始 Shapefile 映射且不是排除行政区；可以更换代码及瓦片数量。
初版清单最多 100000 块；全量多县规划、流式超大清单尚未实现。

先用更小的 16 MiB **大小上限**测通，不会为了凑满包下载范围外瓦片：

```bash
.venv/relay/bin/python cloud_relay.py run jobs/cloud-relay/<plan-id>/plan.json \
  --package-mib 16 --max-packages 1
```

运行账本为 `ledger.json`，包在 `archives/`；默认只生成一个新包并保留瓦片和归档。
同一命令再次执行会校验现有包，不重复下载已核验的瓦片或创建重复包。
`--max-packages 0` 才会继续覆盖计划中的全部包。已存在的待完成包先恢复，再生成新包。
plan、包名、每瓦片 SHA/字节数/CRC、包内 manifest 和包成员清单均核验。
归档采用独立 LZMA2 7z，默认低内存压缩参数 `-mx=3 -md=16m -mmt=2`。
JPEG/PNG 已压缩，不能假设高压有明显收益。

## 官方夸克上传和 1–2 GiB 流转

Agent 必须先取得并完整读取官方 `quarkclouddrive` Skill，再按其要求完成登录和目标目录查询。
本机个人 Skill 不会自动同步到 Codex Cloud；当前代码不会替代这一步，也不使用社区 Cookie API。
把真实目录 FID、Skill 路径、会话 ID 和本次原始用户授权消息文件传给脚本。
授权消息文件放在被 Git 忽略的 `jobs/`，不提交到仓库。

在 Agent 可识别的前台执行：

```bash
.venv/relay/bin/python cloud_relay.py run jobs/cloud-relay/<plan-id>/plan.json \
  --package-mib 1024 --max-packages 0 --threads 8 \
  --upload --release-verified \
  --skill-dir /absolute/path/to/quarkclouddrive \
  --parent-fid REAL_FOLDER_FID \
  --session-input /absolute/path/to/jobs/session-input.txt \
  --session-id UNIQUE_CURRENT_SESSION_ID
```

`--package-mib 1024` 是 1 GiB 成品上限，2048 是 2 GiB，允许 16–2048 MiB。
不要求最后一包达到 1 GiB；包上限含 manifest 和归档开销。
原始下载预算留出至少 1% 或 1 MiB 的开销；若实际成包仍超限，暂停并保留文件，
**初版不自动改写已经登记的包来拆分**，需要重新规划/处理这个异常。
它不是大归档分卷，每个 `.7z` 都可以独立解压。

默认保留至少 1 GiB 安全余量；下载前检查约两倍当前原始数据预算，加操作余量。
归档前再检查实际空间；空间不足会停止，不会清理未上传包以强行继续。
不带 `--release-verified` 就保留本地数据；此模式无法长期流转，只适合调试或容量足够的节点。
清理开关仅适用于本脚本下载的独立任务临时数据，不授权清理原 Windows 瓦片和 TIFF。

上传成功条件：官方上传结果 + 精确文件名/大小/FID 的 fresh listing。
清理前再次请求 fresh listing，核验本地包 SHA 和瓦片哈希；失败保留全部尚存数据。
大包当前采用列表身份/大小核验，**不是远端全文件 SHA 回读**。
receipts 与 ledger 保留；成功清理不会删除 manifest、瓦片元数据或回执。

## 恢复与失败

Linux OS 锁自动在进程退出时释放；同一任务拒绝第二个并行 worker。
每阶段原子写盘，恢复时以文件核验和远端列表为准。
下载失败、损坏响应、归档超限/损坏、磁盘不足、上传异常均记 `failed` 并保留文件。
修复原因后在同一任务前台重跑。

若上传异常后远端已有同名文件但缺少回执，脚本暂停，禁止盲目重复上传。
先由 Agent 根据官方 Skill 查询远端/官方任务记录，选择官方 resume 或核对重建回执；
**初版不会自动执行官方上传 resume**。
清理中断可以恢复：先再次核验云端，然后删除本包仍存的临时文件，不重新下载已上传的数据。

本地状态 `local_complete` 只表示全部计划瓦片在独立本地包中。
`cloud_complete` 表示全部包有云端核验证据；`released` 表示已按清理开关回收临时数据。
上传/恢复 mock 测试不等于真实夸克链路验收。

## 校验和后续工作

在 `sat` 环境运行 `python -m unittest discover -s tests -v`；原 GIS 测试仍需 GIS 依赖。
只测试轻量运行路径可执行 `python -m unittest discover -s tests -p test_cloud_relay.py -v`（需 7z）。
尚待真实云环境官方夸克端到端验证、1 GiB/2 GiB 压力测试、长期状态检查点保存与全量规划。
当前支持“包级确定性脚本执行”，不承诺后台无限持续运行或无人值守重新登录。
