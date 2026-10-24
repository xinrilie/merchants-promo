# Merchant Promo Project — Audit & Fix Summary

## Scope
Reviewed the uploaded project source, applied corrections, ran offline regression tests, and rebuilt the workbook from the local input/cache files. No live scraping was run during final export.

## Code files to replace
Keep each file in the same `engine/` path in the project:
- `engine/promo_categories.py`
- `engine/promo_product_validation.py`
- `engine/promo_catalog_all_merchants.py`
- `engine/promo_multisource_collector.py`
- `engine/enrich_existing_promo_catalog_multisource.py`

Updated/additional tests:
- `engine/test_categories.py`
- `engine/test_evidence_quality.py`
- `engine/test_access_limits.py` (new)

## Fixes
- Restored the broken `fetch_selected_articles` function in the Momsmoney collector; no article pages are requested after listing failure/rate limiting.
- Stops a source run after HTTP 429, server-side 5xx, request timeout, or off-domain redirect. Partial results are discarded so previous good product rows can be preserved.
- Prevents an incomplete Momsmoney scrape from overwriting the last-good review CSV with an empty/partial review list.
- Added source-host validation after redirects for the multi-source collector.
- Reworked the category rules to return only labels in the 29-category taxonomy, including split between packaged snacks and restaurant/ready-to-eat promotions.
- Added a persistent regex compilation cache to the category classifier. This avoids cache churn in Python's small built-in regex cache.
- Fixed source-evidence counting so a dataset/CSV filename does not count as an independent publication.
- Removed duplicate literal keys in the enhancer report dictionary.

## Validation results
- `python -m compileall -q engine`: passed.
- `python engine/promo_names.py`: passed.
- `python engine/promo_multisource_collector.py --self-test`: passed.
- `python engine/test_duplicate_fixes.py`: passed.
- `python -m pytest -q`: **44 passed**.
- AST scan found no duplicate string keys in dictionary literals.
- Category audit: 2,024 local source rows; 29 unique categories; 0 labels outside the reference taxonomy.
- Workbook ZIP integrity test passed.
- Rebuilt local enhancer in about 15 seconds after regex-cache optimization.

## Important data caveat
The final workbook was rebuilt from the local source CSVs/cache already contained in the uploaded project. The report states that the Momsmoney/Kontan scrape was not fresh for 2026-10-23, so the 18-row latest attempt was not loaded as fresh data. This was intentional under the project's freshness gate. The workbook is therefore a refreshed local build, not a claim that live sources were successfully updated today.

## Suggested local verification
From the project root on Windows:
```powershell
python -m compileall -q engine
python -m pytest -q
python engine/enrich_existing_promo_catalog_multisource.py
```
For a live run, use `Jalankan_Promo.bat`; external sites may be rate-limited. The code now stops safely rather than attempting to bypass restrictions.
