# V4O — How to reproduce the G7 scientific cross-evidence candidate

This patch adds a **read-only review checker**. It does not alter any project protocol, simulate a fault, issue approval, build a Vivado snapshot, or run XSim. Nothing in the generated JSON is an authorization.

Inputs are three exact archives: `CNN_PEP_AI_READY_FULL_HANDOFF_2026-11-04.zip`, `cnn_pep_v4m_g7l2_img0000_results_01.zip`, and `cnn_pep_v4n_g7l2_historical_results_01.zip`. Place these in the repository root or provide explicit paths (no archive modification/recompression). The reviewer should compare these archives' SHA256 with the pinned values in the included example `V4O_G7_REVIEW_CANDIDATE.json`.

From repository root in PowerShell, after a SHA256-verified additive installation of the scripts:

```powershell
$env:CNN_PEP_G7_EVIDENCE_ROOT = (Get-Location).Path
python -m unittest discover -s fi/v4/scripts -p "test_review_g7_v4o.py" -v
if ($LASTEXITCODE -ne 0) { throw "STOP: V4O offline tests failed" }

python fi/v4/scripts/review_g7_v4o.py `
  --mode check `
  --handoff .\CNN_PEP_AI_READY_FULL_HANDOFF_2026-11-04.zip `
  --v4m .\cnn_pep_v4m_g7l2_img0000_results_01.zip `
  --v4n .\cnn_pep_v4n_g7l2_historical_results_01.zip
if ($LASTEXITCODE -ne 0) { throw "STOP: V4O review check refused" }

$Out = "fi/results/v4o_g7_review_candidate_01"
if (Test-Path -LiteralPath $Out) { throw "Existing output; refusing overwrite" }
python fi/v4/scripts/review_g7_v4o.py `
  --mode review `
  --handoff .\CNN_PEP_AI_READY_FULL_HANDOFF_2026-11-04.zip `
  --v4m .\cnn_pep_v4m_g7l2_img0000_results_01.zip `
  --v4n .\cnn_pep_v4n_g7l2_historical_results_01.zip `
  --out-dir $Out
if ($LASTEXITCODE -ne 0) { throw "STOP: V4O output generation failed" }
```

Expected marker:

`V4O_G7_CROSS_EVIDENCE_REVIEW_PASS: 40/40 original raw logs; 4/4 witnesses; 16/16 complete L2 negatives; L0=9 L1=20 L2=24; G7 REVIEW PENDING; 0 XSim; FULL400 BLOCKED`

**Safety:** Do not remove, rename, or overwrite existing V4M/V4N outputs or their frozen ancestors. Do not use a V4O review marker to seal G7 or authorize G6. The unit tests read the three ZIPs and temporarily rewrite copies of V4M/V4N for negative testing. They do not touch the originals.
