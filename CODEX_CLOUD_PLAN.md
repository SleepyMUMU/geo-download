# Codex Cloud 瓦片流转节点：方案与研究进度

更新：2026-09-30（北京时间）。本文件是 `codex-cloud-demo` 分支的续作入口。
这是方案和真实进度记录，不是已跑通的证明。

> **实现更新（2026-09-30）**：初版已实现，入口 `cloud_relay.py`。
> 使用与限制见 `CLOUD_RELAY_README.md`，真实结果见 `CLOUD_RELAY_RESULTS.md`。
> 128 瓦片下载/打包/恢复已实测，38 项测试通过；真实夸克上传和 Plus 云节点压力测试仍未完成。
> 下方“本次真实进度”保留为实现前研究快照，最新事实以这两份新增文件为准。

## 本次决定与边界

用户最新决定：Plus 云节点磁盘只有 8 GiB，先测试将其单独作为流转节点。
下载到“本地”在此指云节点的临时磁盘，不是用户的 Windows 电脑。
节点将同一图源的瓦片下载、校验、按约 1–2 GiB 独立包归档、上传夸克，再处理下一包。
优先证明下载与上传的实际性能及可靠性；GM 类似画质的完全脚本化研究暂缓。
全部适配、demo、性能报告只提交到 `codex-cloud-demo`。不改 `master`，不自动合并。

不接管或触碰用户 Windows 上正在进行的 GM 导出、7z 备份、瓦片缓存、锁、上传进程或关机任务。
`CURRENT_TASK.md` 和 `GM_BATCH_README.md` 仍是那条生产任务的入口。

## 推荐总体流程

1. 从已授权原始 Shapefile 范围选择小范围 demo，复用已核验的行政代码映射。
2. 生成固定图源、版本、z/x/y、边界哈希、任务 ID 和包 ID 的 manifest。
3. 检查实际磁盘余量和依赖占用，只下载当前包的数据。
4. 下载、解码检查尺寸和色彩、计算每瓦片 SHA-256，失败重试，检查完整性。
5. 建立独立可解压的 7z 包；默认目标 1 GiB，可配置到 2 GiB。保留 z/x/y 目录和包内 manifest。
6. 用 `7z t` 核验归档，计算包 SHA-256，记录 PNG 数量、原始字节数、归档字节数和耗时。
7. 通过官方 `quarkclouddrive` Skill 在 Agent 可识别前台上传。
8. 独立 fresh browse 核对远端 FID、准确文件名和字节数，记录回执与核验方式。
9. 仅对应 demo 包已通过本地完整性及 fresh cloud listing 后，才允许按本任务保留策略回收该包临时数据；失败一律保留、暂停。
10. 原子写入 ledger，继续下一包。最后核验全部计划瓦片被已上传包唯一覆盖，无漏包、无重复。

首选“独立分包”，不优先使用单一大归档的 `.7z.001/.002` 分卷：
独立包可单独上传、校验、恢复与释放空间；跨卷归档通常需要所有卷才能完整解压和测试。
PNG 已压缩，不能假设高压必然明显变小。包大小必须以成包后的实际字节为准；
若超出 2 GiB 上限，应将当前瓦片集合拆为更小的独立包并重试，不盲目继续上传。

## 8 GiB 磁盘约束

- 8 GiB 是整台默认 VM 的磁盘预算，并非全部可作下载缓存；依赖、仓库和运行时也占空间。
- 同时存在原始瓦片、正在写入的归档、上传临时数据和状态，不能按“每包 2 GiB，所以可同时跑四包”计算。
- 首轮以 1 GiB 为目标，单包串行流转，预留至少 1 GiB 操作空间；真实空间不足立即暂停并记录原因。
- 按“源文件 + 成品包 + 临时开销 + 安全余量”检查余量，不能依赖压缩率预测。
- 流转节点不需要 GDAL、GeoPandas 或整套 GIS 环境。优先最小 Python/Pillow/HTTP/7z 工具链，
  避免本次研究用的完整 GIS 环境挤占未来 8 GiB 节点。
- 不做整县拼接、重投影、参考 RGB 或 GM 导出，不在云节点堆积全量数据。
- 用户要求本轮不设置固定下载速率上限；仍使用有界并发，记录 HTTP 错误、服务端限制和失败重试。
  服务端 429/Retry-After 必须遵守；无界线程并不等于有效性能测试。

## 断点续跑与节省 token

包状态建议：
`planned → downloading → downloaded → archiving → local_verified → uploading → cloud_verified → released`。
失败状态保留阶段、错误和已存在文件；每一步可通过真实文件及哈希重新核验。
任务重启先核验 ledger 和文件，再执行未完成步骤，禁止只看状态文字跳过检查。
上传异常先重新列表并查询官方上传任务记录，再决定 resume；不能盲目重传。

下载、检查、打包、统计由确定性脚本处理；模型主要负责首次配置、权限/登录、异常处理和阶段性结果检查。
不让模型逐瓦片决策，不输出逐瓦片日志，定期报告包级进度。
无人值守能力需要实测，不能因为网页支持电脑休眠后继续工作，就声称任意后台进程可无限运行。
GitHub 保存代码、非敏感配置和精简研究/测试报告；不提交瓦片、包、Cookie、用户原始授权消息或逐文件运行账本。
运行 ledger 应留在节点任务目录，并考虑在远端保存检查点；GitHub 的代码不能替代运行状态。

## 已读取的仓库事实

- 当前主分支为 `master`。
- 初次核验 `master` 与 `codex-cloud-demo` 均指向
  `c805dc84b95bcb51ff0e508b9467b60614244f98`，没有分支差异。
- 旧 GDAL 脚本版 26 县任务已完成；当前生产任务另有 GM 原生导出和原始瓦片备份，不能混淆完成状态。
- 图源：Wayback 58924，z16，256 像素 XYZ，EPSG:3857；版本发布日期不等于当地拍摄日期。
- `config.yaml` 当前依赖 Windows localhost 代理、Windows Bash 路径和本机夸克 Skill。
- `imagery.download` 设置 `Session.trust_env=False`，只使用配置的显式代理。
  云端适配必须处理环境网络代理，不能直接复用 localhost，也不能绕过云环境网络策略。
- `backup_gm_tiles.py` 当前使用硬编码 Windows 路径和约 10 GiB 分包，与此次 1–2 GiB 云节点目标不同。
- `gm_batch.py` 顶层导入 `msvcrt`，现有 GM 测试在 Linux 上直接导入会失败；
  这属于后续平台适配事项，本次尚未改动。
- 仓库要求仅用官方夸克 Skill、真实 parent_fid，并以 fresh listing 核验。
  当前会话没有可用的官方夸克 Skill/工具，未登录、未调用任何非官方上传 API。
- 原任务目标目录在现有配置中为“夸克默认位置/新疆”。demo 也需以真实 FID 操作，
  使用独特 demo 文件名，不覆盖或删除生产文件。
- fresh listing 的 FID/文件名/大小核验不是远端全量 SHA 回读，报告必须区分。
  既有记录提示大文件回读约 50 MiB 限制，不能据此误判上传失败。

## GM 类似效果：保留的研究方向（暂缓）

用户希望找到接近 GM 效果的参数，达到目标后完全脚本化。
已有 GM 生产参数为 `SAMPLING_METHOD=DEFAULT`、`PALETTE=OPTIMIZED`、
5 m UTM、单波段 Byte/256 色、PackBits，无透明背景、分块和金字塔。

官方 GM 文档的搜索结果说明 DEFAULT 会受自动重采样/图层设置影响，
因此不能直接将 DEFAULT 等同于 GDAL average 或 nearest。
OPTIMIZED 表示优化的至多 256 色混合，并没有建立与某一 GDAL/Pillow 算法逐像素等价的证据。
GDAL 官方提供 median-cut 与 Floyd–Steinberg 抖动的 RGB 转调色板路线，
可以与仓库现有“保留稀有蓝青/亮部颜色 + 全局有序抖动”比较。
未来候选维度：nearest/bilinear/average、调色板训练、抖动强度和存储方式。
PackBits/DEFLATE 本身不决定量化颜色，存储选项与画质比较应分开。

尚未生成候选 TIFF、尚未完成与真实 GM 参考 TIFF 的对照、尚未选定替代 GM 的参数。
最终比较必须使用同一原始瓦片和边界、对齐输出像元网格，比较展开后的 RGB 而非调色板索引，
检查湖水/冰雪/亮部、色带与块边界，并区分“接近视觉效果”和“逐像素相同”。

## 云端与计费核实（2026-09-30）

官方文档：
- Cloud environments: https://learn.chatgpt.com/docs/environments/cloud-environments
- Pricing: https://learn.chatgpt.com/docs/pricing
- GDAL RGB-to-palette: https://gdal.org/en/stable/programs/gdal_raster_rgb_to_palette.html
- GM EXPORT_RASTER 搜索入口:
  https://www.bluemarblegeo.com/knowledgebase/global-mapper/cmd/EXPORT_RASTER.htm

已打开的 Cloud environments 文档给出：
Plus/Edu Plus 默认 2 vCPU、8 GiB 内存、8 GiB 磁盘；Pro/Business/Enterprise 默认 4 vCPU、16 GiB、32 GiB。
默认保存状态可恢复至上次启动回合或恢复任务后约七天。
每个新任务获得独立工作区，个人本机 Skill 不会自动同步到云环境。
电脑休眠后云任务可继续，网络域名/凭据需要单独配置。

已打开的 Pricing 文档确认本地和云任务共享订阅额度，模型、上下文、工具调用、
检索、缓存及执行位置均会影响消耗，云任务可能比本地消耗更多。
本次检索未找到足以断言“标准 VM 计算/网络/存储全部只按模型 token 换算、没有其他用量因素”的明确规则。
不能把此前对话中的“只收模型、VM 完全免费”说法当作已核实结论，也不能用 API token 价格直接推算 Plus 订阅额度。
后续应记录实际任务前后 Usage 变化；外部网络和磁盘吞吐与额度数据分开测量。

GM 页面直接打开时重定向到厂商首页，上述 GM 参数释义来自官方文档的搜索摘录，
并结合仓库已验证 GM 脚本；后续应取得完整官方参数说明及实际参考图再定替代参数。

## 本次真实进度

已完成：
- 核实分支与主线起点，读取 AGENTS/README/HANDOFF/config/CURRENT_TASK/GM_BATCH_README 及核心代码。
- 克隆独立分支到当前 Work 会话工作区。
- 在隔离 `sat` 环境安装研究所需依赖：Python 3.12、GDAL 3.11.5、GeoPandas、Pillow、mercantile、7z 等；
  没有修改全局 Python。此环境仅用于研究，不作为轻量流转节点的依赖方案。
- 核对官方云端规格和订阅用量说明。
- 将当前方向改为先做轻量流转节点，暂停 GM 参数实验。

尚未完成：
- 未编写流转节点代码，未改现有生产配置或脚本。
- 未下载 demo 瓦片、未打包、未上传夸克、未清理生产或 demo 数据。
- 未运行仓库测试；尚无网络吞吐、压缩时间、内存峰值、成功恢复或上传性能结果。
- 未创建/发布用户账户中的 Codex Cloud 环境，未验证云节点夸克官方 Skill 可用。
- 当前 Work scratch 工作区显示约 32 GiB 磁盘，不能冒称为 Plus 8 GiB Codex Cloud 的实测环境。
- 无端到端成功证据，不可报告“demo 已跑通”或“适合全量无人值守”。

## 下一次接手顺序

1. 先读本文件和 AGENTS，确认只在 `codex-cloud-demo` 分支操作。
2. 实现独立入口/配置，不复用生产 jobs、输出、缓存或锁；使用最小依赖。
3. 先做小包连通性 demo（数十到数百瓦片），记录节点资源、实际网络/缓存来源和阶段耗时。
4. 接入并读取官方夸克 Skill，确认 Agent 前台身份、登录和目标 FID；无法取得时只验下载/打包并明确上传未验。
5. 小包端到端验证后测试 1 GiB，再按真实可用空间选择是否测试 2 GiB。
6. 验断点重启、坏瓦片重下、归档损坏、磁盘不足、上传异常/登录过期和 fresh listing 不符；
   覆盖数据安全相关测试，执行仓库规定的测试并如实说明 Linux 平台缺口。
7. 将精简可复现命令、测试报告、失败原因和下一步写回此分支。
8. 流转路线稳定后，再恢复 GM 类似画质实验；主线合并由用户决定。
