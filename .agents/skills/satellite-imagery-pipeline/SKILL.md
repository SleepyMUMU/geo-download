# SKILL.md
---
name: satellite-imagery-pipeline
description: Encapsulates the satellite imagery download, processing, upload, verification, and cleanup workflow as an Antigravity skill.
---

## Overview
This skill automates the end‑to‑end pipeline for downloading high‑resolution satellite tiles, correcting coordinate systems, resampling & compressing them, uploading to Quark (Kuake) cloud storage, verifying the upload, and finally syncing the codebase to GitHub. It is designed to **robust** with automatic retry, pre‑flight coordinate correction, and clear user reporting.

## Prerequisites
- Python 3.9+ with the project dependencies installed (GDAL, geopandas, etc.)
- The project directory must be located at `D:/WorkSpace/AI/Geo Download` (or set the `SCRIPT_DIR` environment variable accordingly).
- `kuake` CLI installed and authenticated for Quark cloud storage.
- `git` and `gh` CLI configured for the target GitHub repository.
- Optional: custom tile source URL (defaults to the ArcGIS Wayback service).

## Parameters
| Parameter | Description | Required | Default |
|-----------|-------------|----------|---------|
| `province` | Chinese province name (e.g., `辽宁`) | ✅ | – |
| `source_url` | Tile source base URL (supports ArcGIS, Gaode, Alibaba) | ❌ | ArcGIS Wayback URL pre‑configured in `config.yaml` |
| `compression` | GDAL compression level (0‑9) | ❌ | `9` |
| `color_space` | Output color space (`RGB`, `PCT`, etc.) | ❌ | `PCT` |
| `retry_limit` | Number of automatic retries for download/compress/upload failures | ❌ | `1` |

## Execution Steps
1. **Coordinate Pre‑check & Correction**
   - Run `convert_gcj02_to_wgs84.py` on the province shapefile.
   - If the shapefile already uses WGS‑84, the script exits silently.
   - Any error is reported to the user and halts further processing.

2. **City Discovery**
   - Load `DEFAULT_CITY_SHP` and extract all **city names** belonging to the given province.
   - Skip cities that already have a final `*.tif` in `output/` and record them as **already processed**.

3. **Asynchronous Dual‑Engine Pipeline**
   - **Downloader** thread pool (max 8 workers) pulls tiles for each city using the chosen `source_url`.
   - **Processor** consumes from the queue, generating a temporary config YAML, then runs `process_tiles.py` to:
     - Merge tiles (VRT)
     - Resample to 5 m resolution (Lanczos)
     - Convert to the desired color space
     - Apply DEFLATE compression (`ZLEVEL=9`, `PREDICTOR=1`, `BIGTIFF=IF_NEEDED`)
     - Build overviews/pyramids
   - On **success**, the city is queued for upload. On **failure**, the step is **retried once** (controlled by `retry_limit`). If it still fails, the city is added to `fail_cities` and the user is notified.

4. **Asynchronous Upload**
   - A single‑thread `ThreadPoolExecutor` uploads each successful city via `upload_to_quark.py`.
   - Upload logs are saved under `logs/` with the pattern `<city>_upload.log`.
   - Failed uploads are retried once; persistent failures are recorded in `upload_fail`.

5. **Cloud Verification (Kuake)**
   - After **all** uploads finish, run `kuake list` (or `kuake ls`) on the target Quark folder.
   - Compare the remote file list with the local `output/` directory.
   - If any city is missing or the file size differs, **report to the user** and **do not proceed to cleanup**.
   - If verification passes, continue.

6. **GitHub Sync**
   - Stage any changed configuration or scripts.
   - Commit with message `"[pipeline] Update after processing {province}"`.
   - Push to the remote repository using `git push` (or `gh repo sync`).
   - If the `gh` CLI is not found, the skill **restarts the terminal** (powershell‑windows skill) and retries.

7. **Final Cleanup**
   - Delete temporary config files (`_temp_config_*.yaml`).
   - Remove intermediate tile caches under `cache/`.
   - Optionally, delete original downloaded tiles if the user sets `cleanup=true`.

## Reporting
- A concise **summary** is printed at the end of execution, showing:
  - Total cities discovered, processed, skipped, failed (download/compress) and failed uploads.
  - Any verification mismatches.
- If verification fails, the skill **pauses** and prompts the user to either retry the upload step or abort.

## Usage Example
```bash
# Activate the skill (Antigravity CLI)
agy run satellite-imagery-pipeline --province 辽宁 --source_url https://example.com/tiles --compression 8
```
The skill will walk through the steps automatically, handling retries and reporting progress.

## Extensibility
- To add a new tile source, extend `config.yaml` with the appropriate URL template.
- Adjust `max_threads` in `run_province.py` if a different concurrency level is needed.
- The skill can be invoked from other agents via `invoke_subagent` by referencing its name.

---
*End of skill definition*
