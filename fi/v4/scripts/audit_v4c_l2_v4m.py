#!/usr/bin/env python3
"""Read-only replay of pinned historical V4c raw evidence against V4M outputs."""
import argparse
import sys
from pathlib import Path
import analyze_v4c_l2_v4m as m

EXPECTED={'v4m_g7_l2_summary.json','v4m_g7_l2_40.csv','V4M_G7_L2_REPORT.md','SHA256SUMS.txt'}


def inspect(analysis, src):
    analysis=Path(analysis)
    m.require(analysis.is_dir() and not analysis.is_symlink(), 'Missing or symlinked analysis directory')
    m.require({p.name for p in analysis.iterdir()}==EXPECTED, 'Incomplete/extra V4M artifacts')
    for f in analysis.iterdir():
        m.require(f.is_file() and not f.is_symlink(), 'Non-regular/symlink result artifact: '+f.name)
    fresh=m.compute(src)
    expected=m.output_bytes(fresh)
    manifest=''.join(m.sha(b)+'  '+n+'\n' for n,b in sorted(expected.items())).encode('ascii')
    expected['SHA256SUMS.txt']=manifest
    for fname, bytes_expected in expected.items():
        m.require((analysis/fname).read_bytes()==bytes_expected, 'V4M result mismatch: '+fname)
    return fresh,expected


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--handoff-zip')
    source.add_argument('--repo-root')
    p.add_argument('--analysis-dir',required=True)
    p.add_argument('--out-dir',help='Optional fresh read-only audit receipt directory')
    a=p.parse_args(argv)
    try:
        src=m.Source(a.handoff_zip,a.repo_root)
        try: fresh,expected=inspect(a.analysis_dir,src)
        finally: src.close()
        if a.out_dir:
            audit=dict(schema='CNN_PEP_V4M_G7L2_READONLY_REPLAY_AUDIT_V1',
                       finding='40_SHA_PINNED_RAW_LOGS_L2_INCREMENT_4_CONFIRMED',
                       analyzed_scenarios=40,increment_l2_over_l1=4,
                       original_archive_or_repo_rechecked=True,
                       analysis_file_sha256={f:m.sha(b) for f,b in sorted(expected.items())},
                       raw_feature_bytes_independently_recomputed=False,
                       g7_approved=False,g6_qualified=False,full400_authorized=False,new_xsim_runs=0)
            out=m.create(a.out_dir,{'v4m_g7_l2_audit_receipt.json':m.canonical(audit)})
            print('V4M_G7_L2_AUDIT_OUTPUT:',out)
        print('V4M_G7_L2_REPLAY_AUDIT_PASS: 40/40 SHA-pinned historical logs; '
              'L2-only=4; canonical analysis exact; 0 XSim; FULL400 BLOCKED')
        return 0
    except (ValueError,KeyError,FileNotFoundError,OSError,m.zipfile.BadZipFile) as e:
        print('V4M_G7_L2_AUDIT_FAIL_CLOSED:',e,file=sys.stderr)
        return 2

if __name__=='__main__':
    sys.exit(main())
