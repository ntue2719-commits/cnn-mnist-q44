"""Independent V4f4C audit fixture tests. 0 XSim; no generated FI evidence."""
from __future__ import annotations
from pathlib import Path
import io
from contextlib import redirect_stdout
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import qualify_xsim_failures_v4f4c as q
import audit_v4f4c as a
import qualify_runner_v4d as v4da
from pilot400_common import FIELDS,read_csv,write_csv,sha256

REAL_REPO=HERE.parents[2]

class HealthyMockFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name)
        self.results=self.repo/'fi/results';self.results.mkdir(parents=True)
        self.scenarios=self.repo/'fi/scenarios';self.scenarios.mkdir()
        shutil.copyfile(REAL_REPO/'fi/scenarios/pilot400.csv',self.scenarios/'pilot400.csv')
        (self.repo/'data').mkdir()
        self.src=self.repo/'mock_input';self.src.write_bytes(b'mock input')
        self.rows=q.select_canary(read_csv(self.scenarios/'pilot400.csv',FIELDS))
        self.row=self.rows[0]
        self.attempt=self.results/'v4f4c_mockhealthy_01';self.attempt.mkdir()
        write_csv(self.attempt/'canary_selection.csv',FIELDS,self.rows)
        self.runs=self.attempt/'runs';self.runs.mkdir()
        sid=self.row['scenario_id']
        r=self.row
        self.paths={kind:self.runs/(sid+ext) for kind,ext in
                    (('cmd','.cmd'),('log','.log'),('console','.console.log'),
                     ('wrapper','.wrapper.log'))}
        self.paths['cmd'].write_text('call "C:/Xilinx/xsim.bat" -tclbatch "tcl" '+
                    ' '.join(f'-testplusarg "{key}={value}"' for key,value in
                        [('FI_OFFSET',r['injection_offset']),('SCENARIO_ID',sid),
                        ('TARGET_ID',r['target_id']),('FI_BIT',r['bit_index']),
                        ('SAMPLE_ID',r['sample_id'])] +
                        [(key,(self.repo/'fi/pilot400/samples'/r['sample_id']/name).resolve().as_posix())
                         for key,name in (('INPUT_FILE','input.hex'),
                          ('POOL1_FILE','golden_pool1.hex'),
                          ('POOL2_FILE','golden_pool2.hex'),
                          ('LOGITS_FILE','golden.hex'),
                          ('PREDICTION_FILE','prediction.hex'))]))
        self.paths['log'].write_text('FI_APPLIED scenario='+sid+'\n'+
                                     'FI_RESULT_V2,'+sid+'\n')
        self.paths['console'].write_text('XSim completed successfully\n')
        self.paths['wrapper'].write_text('wrapper exit 0\n')
        # Synthetic compiled snapshot/Job receipts: only for AUDITOR UNIT TESTS.
        build=self.attempt/'build_001';build.mkdir()
        for name,text in [('source_vlog.prj','mock project source\n'),
                          ('compile_snapshot.log','synthetic xvlog OK\n'),
                          ('elaborate_snapshot.log','synthetic xelab OK\n'),
                          ('runall.tcl','run all\nquit\n')]:
            (build/name).write_text(text)
        toolchain={}
        for name in ('vivado.bat','xvlog.bat','xelab.bat','xsim.bat'):
            f=self.repo/name;f.write_text('synthetic mock binary '+name)
            toolchain[str(f)]=sha256(f)
        process_hashes={}
        for stage in ('compile','elaborate'):
            stub={'return_code':0,'external_timeout':False,
                  'termination_requested':False,
                  'owned_active_processes_after':0,'job_object_kill_on_close':True}
            child={'cmd_pid':1234,'complete':True,'return_code':0}
            names={'job_process_sha256':stage+'_job_process.json',
                   'cmd_sha256':stage+'_snapshot.cmd',
                   'wrapper_sha256':stage+'_wrapper.log',
                   'job_launch_sha256':stage+'_job_launch.json',
                   'child_lifecycle_sha256':stage+'_child_lifecycle.json'}
            for suffix,name in names.items():
                f=build/name
                if suffix=='job_process_sha256':v4da.atomic_write_json(f,stub)
                elif suffix=='child_lifecycle_sha256':v4da.atomic_write_json(f,child)
                else:f.write_text('synthetic '+stage+' '+suffix)
                process_hashes[stage+'_'+suffix]=sha256(f)
        self.mock_build={'prj_sha256':sha256(build/'source_vlog.prj'),
              'compile_sha256':sha256(build/'compile_snapshot.log'),
              'elab_sha256':sha256(build/'elaborate_snapshot.log'),
              'tcl_sha256':sha256(build/'runall.tcl'),
              'build_strategy':'V4F4C_TWO_PRIVATE_JOB_OBJECTS_PINNED_V3A_PRJ',
              'source_hashes':{str(self.src):sha256(self.src)},
              'toolchain_hashes':toolchain,'build_process_hashes':process_hashes}
        v4da.atomic_write_json(self.attempt/'job_launch.json',{'private_job_assigned':True})
        v4da.atomic_write_json(self.attempt/'child_lifecycle.json',{'cmd_pid':1234,'complete':True})
        self.process={'return_code':0,'classification':'RAW_RESULT_ELIGIBLE',
                      'launcher_pid':1222,'child_started':True,
                      'child_receipt_sha256':sha256(self.attempt/'child_lifecycle.json'),
                      'job_launch_sha256':sha256(self.attempt/'job_launch.json'),
                      'owned_active_processes_after':0,
                      'job_object_kill_on_close':True,'marker_evidence':[1,1],
                      'termination_requested':False,'external_timeout':False}
        self.validation={'scenario_id':sid,'outcome':'FINAL_OUTPUT_MATCH',
                         'injection_verified':'1'}
        self.meta={'schema':q.SCHEMA,'status':q.SUCCESS,
              'execution_scope':'ONE_FROZEN_CANARY_XSIM_MAX',
              'source_authority':'vivado','trial':'healthy',
              'scenario_id':sid,'scenario':r,
              'canary_sha256':sha256(self.attempt/'canary_selection.csv'),
              'input_pins':{str(self.src):sha256(self.src)},
              'max_xsim_processes':1,'xsim_invocations_budgeted':1,
              'xsim_invocations_committed':1,
              'full400_authorized':False,'full400_runner_qualified':False,
              'full400_executed':False,'scientific_protocol_approved':False,
              'full400_gate':'BLOCKED_G0_G7_REQUIRED',
              'validation':self.validation,'build':self.mock_build,'process':self.process,
              'source_v3a_prj_sha256':sha256(build/'source_vlog.prj'),
              'run_hashes':{k:sha256(p) for k,p in self.paths.items()},
              'cmd_sha256':sha256(self.paths['cmd'])}
        v4da.atomic_write_json(self.attempt/q.RECEIPT,self.process)
        self.meta['process_receipt_sha256']=sha256(self.attempt/q.RECEIPT)
        self._commit()
    def _commit(self):v4da.atomic_write_json(self.attempt/'attempt.json',self.meta)
    def _audit(self, suffix='a'):
        # Strict parser is mocked only for fixture; never use mock as FI evidence.
        with (patch.object(q.v4f2,'strict_result',return_value=self.validation),
              redirect_stdout(io.StringIO())):
            return a.audit_attempt(self.repo,self.results,self.attempt,
                               self.results/('v4f4c_audit_'+suffix))
    def test_healthy_fixture_valid(self):
        receipt=self._audit()
        self.assertEqual(receipt['result'],'ONE_REAL_XSIM_MECHANISM_VERIFIED')
        self.assertEqual(receipt['new_xsim_runs_during_audit'],0)
    def test_mutated_log_rejected(self):
        self.paths['log'].write_text('corrupted')
        with self.assertRaises(ValueError):self._audit()
    def test_mutated_cmd_rejected(self):
        self.paths['cmd'].write_text('different')
        with self.assertRaises(ValueError):self._audit()
    def test_mutated_console_rejected(self):
        self.paths['console'].write_text('ERROR')
        with self.assertRaises(ValueError):self._audit()
    def test_mutated_wrapper_rejected(self):
        self.paths['wrapper'].write_text('changed')
        with self.assertRaises(ValueError):self._audit()
    def test_orphan_run_file_rejected(self):
        (self.runs/'another.log').write_text('orphan')
        with self.assertRaises(ValueError):self._audit()
    def test_inflight_leftover_rejected(self):
        (self.attempt/q.INFLIGHT).write_text('orphan')
        with self.assertRaises(ValueError):self._audit()
    def test_missing_process_receipt_rejected(self):
        (self.attempt/q.RECEIPT).unlink()
        with self.assertRaises(ValueError):self._audit()
    def test_source_pin_changed_rejected(self):
        self.src.write_bytes(b'modified')
        with self.assertRaises(ValueError):self._audit()
    def test_forged_release_rejected(self):
        self.meta['full400_runner_qualified']=True;self._commit()
        with self.assertRaises(ValueError):self._audit()
    def test_duplicated_injection_markers_strict_parser_is_real(self):
        # Replacing test fixture parser with the real strict parser must reject
        # the deliberately incomplete synthetic FI_RESULT_V2 CSV format.
        with self.assertRaises((ValueError,KeyError,IndexError)):
            q.v4f2.strict_result(self.paths['log'],self.row)
    def test_process_receipt_contents_changed_rejected(self):
        self.process['return_code']=77
        v4da.atomic_write_json(self.attempt/q.RECEIPT,self.process)
        with self.assertRaises(ValueError):self._audit()
    def test_wrong_input_sample_hex_plusarg_rejected(self):
        text=self.paths['cmd'].read_text()
        self.paths['cmd'].write_text(text.replace('/IMG0003/input.hex',
                                                 '/IMG0006/input.hex'))
        self.meta['run_hashes']['cmd']=sha256(self.paths['cmd'])
        self.meta['cmd_sha256']=sha256(self.paths['cmd'])
        self._commit()
        with self.assertRaises(ValueError):self._audit()
    def test_missing_compiler_stage_receipt_rejected(self):
        (self.attempt/'build_001/compile_job_process.json').unlink()
        with self.assertRaises((ValueError,OSError)):self._audit()
    def test_bad_compiler_job_status_rejected(self):
        p=self.attempt/'build_001/compile_job_process.json'
        v4da.atomic_write_json(p,{'return_code':9})
        self.meta['build']['build_process_hashes']['compile_job_process_sha256']=sha256(p)
        self._commit()
        with self.assertRaises(ValueError):self._audit()
    def test_attempt_symlink_rejected(self):
        link=self.attempt/'linked.txt'
        try:link.symlink_to(self.src)
        except OSError:self.skipTest('Host disallows symlinks')
        with self.assertRaises(ValueError):self._audit()
    def test_missing_child_job_sha_rejected(self):
        self.meta['process'].pop('child_receipt_sha256')
        v4da.atomic_write_json(self.attempt/q.RECEIPT,self.meta['process'])
        self.meta['process_receipt_sha256']=sha256(self.attempt/q.RECEIPT)
        self._commit()
        with self.assertRaises(ValueError):self._audit()
    def test_bad_job_process_accounting_rejected(self):
        self.meta['process']['owned_active_processes_after']=1;self._commit()
        with self.assertRaises(ValueError):self._audit()

if __name__=='__main__':unittest.main()
