#!/usr/bin/env python3
"""Render optional V4P publication figures from a SHA-audited summary JSON.

Never executes Vivado/XSim; generates model-only charts with a clear evidence label.
Requires matplotlib (optional), in addition to the project's existing NumPy.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--summary', type=Path, required=True)
    p.add_argument('--out-dir', type=Path, required=True)
    args=p.parse_args()
    source=args.summary.read_bytes()
    data=json.loads(source)
    if data.get('schema') != 'CNN_PEP_V4P_HISTORICAL_OFFLINE_NOVELTY_CANDIDATE_V1' or data.get('full400_authorized') is not False or data.get('xsim_new_runs') != 0:
        raise ValueError('Input is not a pinned, bounded offline V4P candidate')
    if args.out_dir.exists():
        raise ValueError('Fresh-only: output exists')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    args.out_dir.mkdir(parents=True)
    keys=['L0','L1','L1_P1','L1_P2','L2']
    labels=['L0','L1','L1 + POOL1','L1 + POOL2','L2']
    counts=[data['observability'][x].get('TRUE',0) for x in keys]
    fig,ax=plt.subplots(figsize=(8.1,4.8))
    bars=ax.bar(range(5),counts,width=.66)
    ax.set_xticks(range(5),labels)
    ax.set_ylim(0,40)
    ax.set_ylabel('Scenarios with observable error (out of 40)')
    ax.set_title('Paired stage-observer ablation: directed IMG0000 cohort')
    ax.grid(axis='y',alpha=.22)
    ax.set_axisbelow(True)
    for bar,n in zip(bars,counts):
        ax.text(bar.get_x()+bar.get_width()/2,n+.65,str(n)+'/40',ha='center',fontsize=10)
    fig.text(.08,.02,'Historical V4c testbench counters; not physical SEU rates or on-chip detection.',fontsize=9)
    fig.tight_layout(rect=(0,0.06,1,1))
    fig.savefig(args.out_dir/'V4P_FIG_OBSERVER_ABLATION.png',dpi=170)
    plt.close(fig)
    rows=[r for r in data['counterfactual']['scenarios']
          if r['scenario_id'] in ('FI_P400_0101','FI_P400_0301','FI_P400_0302')]
    levels=['Conv2 raw','Conv2 quant','Conv2 ReLU','POOL2','Logits']
    fields=['conv2_raw_changed_values','conv2_requant_changed_values',
            'conv2_post_relu_changed_values','pool2_changed_values','logits_changed_values']
    fig,ax=plt.subplots(figsize=(8.4,4.8))
    for row in rows:
        ax.plot(range(5),[row[x] for x in fields],marker='o',label=row['scenario_id'])
    ax.set_xticks(range(5),levels)
    ax.set_ylim(-2,72)
    ax.set_ylabel('Changed values vs bit-exact golden (model)')
    ax.set_title('One-byte POOL1 counterfactual: hypotheses only')
    ax.grid(alpha=.22)
    ax.legend()
    fig.text(.075,.02,'Not a full faulty RTL tensor trace; DAT byte positions are predicted, not directly dumped.',fontsize=9)
    fig.tight_layout(rect=(0,0.06,1,1))
    fig.savefig(args.out_dir/'V4P_FIG_COUNTERFACTUAL_HYPOTHESES.png',dpi=170)
    plt.close(fig)
    print('V4P_OFFLINE_FIGURES_PASS: 2 figures; counterfactual != faulty RTL; 0 XSim')


if __name__ == '__main__':
    main()
