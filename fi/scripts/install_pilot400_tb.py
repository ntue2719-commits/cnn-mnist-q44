#!/usr/bin/env python3
"""Register the standalone Pilot400 TB in Vivado sim_1; NEVER in synthesis sources."""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
from run_pilot400 import safe_tcl


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--project',type=Path,required=True)
    p.add_argument('--vivado',default='vivado')
    p.add_argument('--execute',action='store_true')
    args=p.parse_args()
    repo=args.repo.resolve();project=args.project.resolve()
    source=repo/'fi/tb/tb_cnn_mnist_fi_pilot400.v'
    if not source.is_file():
        raise ValueError('Missing standalone pilot TB: '+str(source))
    dest=repo/'fi/results/pilot400_setup'
    dest.mkdir(parents=True,exist_ok=True)
    done=dest/'installed.done';tcl=dest/'install_tb.tcl'
    source_posix=source.resolve().as_posix()
    tcl.write_text(
        f'open_project {safe_tcl(project.as_posix())}\n'
        'set sim [get_filesets sim_1]\n'
        'if {[llength $sim] != 1} {error {Expected sim_1}}\n'
        f'set src {safe_tcl(source_posix)}\n'
        'set already [get_files -quiet -of_objects $sim]\n'
        'set found 0\n'
        'foreach existing $already {\n'
        '    if {[file tail $existing] eq {tb_cnn_mnist_fi_pilot400.v}} {set found 1}\n'
        '}\n'
        'if {!$found} {add_files -fileset sim_1 -norecurse $src}\n'
        'update_compile_order -fileset sim_1\n'
        '# Vivado project-source changes are saved automatically.\n'
        f'set fp [open {safe_tcl(done.resolve().as_posix())} w]\n'
        'puts $fp {PILOT400_TB_INSTALLED_IN_SIM1}\n'
        'close $fp\n'
        'close_project\n'
        'exit 0\n',encoding='utf-8')
    if not args.execute:
        print('PILOT400_INSTALL_DRY_RUN: generated only '+str(tcl))
        return
    if not project.is_file():
        raise ValueError('Vivado .xpr missing: '+str(project))
    done.unlink(missing_ok=True)
    cp=subprocess.run([args.vivado,'-mode','batch','-source',str(tcl),'-nolog','-nojournal'],
                      cwd=project.parent,check=False)
    if cp.returncode!=0 or not done.exists() or done.read_text().strip()!='PILOT400_TB_INSTALLED_IN_SIM1':
        raise ValueError('Vivado TB installation failed')
    print('PILOT400_TB_INSTALLED_IN_SIM1; synthesis sources unchanged')

if __name__=='__main__':
    try:main()
    except ValueError as exc:raise SystemExit('PILOT400_INSTALL_FAIL: '+str(exc))
