# Geo Download

先读 README.md、HANDOFF.md、config.yaml。以磁盘任务清单交接，不依赖历史对话。
当前 GM 原生导出及瓦片 7z 备份以 CURRENT_TASK.md、GM_BATCH_README.md 和实时状态为准；不要把旧影像任务 26/26 完成误当成 GM 新任务完成。
本项目与主线学习仓库独立；不要修改学习笔记或继承其 Git 自动推送授权。
只处理用户明确选择的原始 Shapefile 文件范围。当前默认范围及合并文件映射以 README.md 和 xinjiang_batch.py 为准。
使用 sat 环境，不改全局环境。修改代码后运行 python -m unittest discover -s tests -v。
用户已恢复夸克上传并选择默认位置下的新疆文件夹；必须使用官方 quarkclouddrive Skill、真实 parent_fid 和云端重新列表核验。不得因上传日志或本地回执删除本地文件。
