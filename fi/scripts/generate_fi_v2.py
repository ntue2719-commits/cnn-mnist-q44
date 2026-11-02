#!/usr/bin/env python3
"""Generate fixed-hierarchy RTL FI dispatch from the source-verified catalog.

Usage:
  python generate_fi_v2.py [--catalog fi_targets_candidates_v2.csv] [--out tb_cnn_mnist_fi_campaign_v2.v]

No dynamically constructed hierarchical paths exist in the generated Verilog.
Only already-declared, source-verified RTL state entries are dispatched.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

HEAD = r'''`timescale 1ns / 1ps
`default_nettype none
// ============================================================================
// CNN_PEP RTL Fault Injection Campaign v2 (XSim / simulation only)
//
// - P0 DUT is unchanged.
// - One transient RTL state-bit inversion per inference, via procedural deposit
//   at a falling-edge + 1ns, immediately before the designated rising edge.
// - No force/release: the injected bit persists until a normal RTL write.
// - The full 80-entry catalog is statically dispatched (generated below).
// - FI-disabled control runs first, with Pool1/Pool2/logits/prediction checks.
// - Each faulty run ends at a fixed start+3000-cycle observation deadline.
// - Invalid injections abort the simulation and emit NO FI_RESULT_V2 line.
//
// NOTE: Designed from uploaded RTL. XSim compile/simulation not verified here.
// Verify deposit semantics on simulator waveforms for each target class.
//
// Plusargs:
//   FI_OFFSET=125 TARGET_ID=CTL_0001 FI_BIT=2 SCENARIO_ID=FI_CTL_0005
//   SAMPLE_ID=IMG0000 INPUT_FILE=<path> POOL1_FILE=<path>
//   POOL2_FILE=<path> LOGITS_FILE=<path> PREDICTION_FILE=<path>
//
// TB top: tb_cnn_mnist_fi_campaign_v2
// ============================================================================

module tb_cnn_mnist_fi_campaign_v2;
    localparam integer INPUT_PIXELS     = 784;
    localparam integer POOL1_WORDS     = 169;
    localparam integer POOL2_WORDS     = 25;
    localparam integer GOLDEN_LATENCY  = 1342;
    localparam integer MAX_WAIT_CYCLES = 3000;

    parameter FC_WEIGHT_FILE = "D:/Share_Data_ubuntu_window/NKKH/data/fc_weight.hex";
    parameter FC_BIAS_FILE   = "D:/Share_Data_ubuntu_window/NKKH/data/fc_bias.hex";

    localparam integer OUTCOME_FINAL_MATCH   = 1;
    localparam integer OUTCOME_WRONG_LOGITS  = 2;
    localparam integer OUTCOME_WRONG_LABEL   = 3;
    localparam integer OUTCOME_PROTOCOL      = 4;
    localparam integer OUTCOME_NONCOMPLETION = 5;

    reg i_clk = 1'b0;
    always #5 i_clk = ~i_clk;

    reg       i_rst_n = 1'b1;
    reg       i_start = 1'b0;
    reg       i_valid = 1'b0;
    reg [7:0] i_pixel = 8'd0;
    wire       o_busy;
    wire       o_valid;
    wire       o_done;
    wire [3:0] o_digit;

    cnn_mnist_top #(
        .FC_WEIGHT_FILE(FC_WEIGHT_FILE),
        .FC_BIAS_FILE(FC_BIAS_FILE)
    ) dut (
        .i_clk(i_clk), .i_rst_n(i_rst_n),
        .i_start(i_start), .i_valid(i_valid), .i_pixel(i_pixel),
        .o_busy(o_busy), .o_valid(o_valid), .o_done(o_done),
        .o_digit(o_digit)
    );

    reg [7:0]  input_mem   [0:783];
    reg [7:0]  golden_p1   [0:675];
    reg [7:0]  golden_p2   [0:199];
    reg [31:0] golden_fc   [0:9];
    reg [7:0]  golden_pred [0:0];

    reg [8*256-1:0] input_file;
    reg [8*256-1:0] pool1_file;
    reg [8*256-1:0] pool2_file;
    reg [8*256-1:0] logits_file;
    reg [8*256-1:0] prediction_file;
    reg [8*64-1:0] scenario_id;
    reg [8*64-1:0] sample_id;
    reg [8*64-1:0] target_id;
    reg [8*4-1:0] target_class;
    reg [8*32-1:0] first_bad_stage;

    integer fi_offset;
    integer fi_bit;
    integer global_cycle;
    integer start_cycle;
    integer first_done_cycle;
    integer first_done_pixels;
    integer pixels_driven;
    integer pixels_accepted;
    integer p1_words, p2_words, fc_valid_count, argmax_count;
    integer conv1_done_count, conv2_done_count, fc_done_count;
    integer done_count;
    integer protocol_error;
    integer pool1_mismatch, pool2_mismatch, logits_mismatch;
    integer checks, errors;
    integer monitor_enable;
    integer fi_requested_cycle, fi_actual_cycle;
    integer fi_events, fi_injection_ok;
    integer outcome;
    integer protocol_ok;
    integer target_width;
    integer i;
    integer j;
    integer completed;
    integer prediction_at_done;
    integer fault_run;

    // Extended to 320 bits so ALL catalog widths can be compared bit-exactly.
    reg [319:0] fi_before_word, fi_after_word, fi_xor_mask;
    reg fi_old_bit, fi_new_bit;

    function [8*32-1:0] outcome_name;
        input integer code;
        begin
            case (code)
                OUTCOME_FINAL_MATCH:   outcome_name = "FINAL_OUTPUT_MATCH";
                OUTCOME_WRONG_LOGITS:  outcome_name = "WRONG_LOGITS_SAME_LABEL";
                OUTCOME_WRONG_LABEL:   outcome_name = "WRONG_LABEL";
                OUTCOME_PROTOCOL:      outcome_name = "PROTOCOL_VIOLATION";
                OUTCOME_NONCOMPLETION: outcome_name = "NONCOMPLETION";
                default:               outcome_name = "INVALID";
            endcase
        end
    endfunction

    task clear_stats;
        begin
            start_cycle=-1;
            first_done_cycle=-1;
            first_done_pixels=-1;
            pixels_driven=0;
            pixels_accepted=0;
            p1_words=0;
            p2_words=0;
            fc_valid_count=0;
            argmax_count=0;
            conv1_done_count=0;
            conv2_done_count=0;
            fc_done_count=0;
            done_count=0;
            protocol_error=0;
            pool1_mismatch=0;
            pool2_mismatch=0;
            logits_mismatch=0;
            first_bad_stage="NONE";
            prediction_at_done=-1;
            fi_requested_cycle=-1;
            fi_actual_cycle=-1;
            fi_events=0;
            fi_injection_ok=0;
            outcome=0;
            protocol_ok=0;
            completed=0;
        end
    endtask

    // Count input acceptance before DUT NBAs, then sample DUT outputs after
    // NBAs (#2). This avoids confusing a newly asserted o_valid with a
    // transaction consumed at the same edge.
    always @(posedge i_clk) begin
        if (!i_rst_n)
            global_cycle = 0;
        else
            global_cycle = global_cycle + 1;

        if (i_rst_n && monitor_enable && i_valid &&
            dut.u_conv1_layer.pixel_valid &&
            !dut.u_conv1_layer.u_conv1_buf.i_start)
            pixels_accepted = pixels_accepted + 1;

        #2;
        if (i_rst_n && monitor_enable) begin
            if (dut.conv1_out_valid) begin
                if (p1_words >= POOL1_WORDS)
                    protocol_error = 1;
                else begin
                    for (j=0; j<4; j=j+1) begin
                        checks = checks + 1;
                        if (dut.conv1_out_data[j*8 +: 8] !==
                            golden_p1[p1_words*4+j]) begin
                            pool1_mismatch = pool1_mismatch + 1;
                            if (first_bad_stage == "NONE")
                                first_bad_stage = "POOL1";
                        end
                    end
                end
                p1_words = p1_words + 1;
            end

            if (dut.conv2_out_valid) begin
                if (p2_words >= POOL2_WORDS)
                    protocol_error = 1;
                else begin
                    for (j=0; j<8; j=j+1) begin
                        checks = checks + 1;
                        if (dut.conv2_out_data[j*8 +: 8] !==
                            golden_p2[p2_words*8+j]) begin
                            pool2_mismatch = pool2_mismatch + 1;
                            if (first_bad_stage == "NONE")
                                first_bad_stage = "POOL2";
                        end
                    end
                end
                p2_words = p2_words + 1;
            end

            if (dut.fc_valid) begin
                for (j=0; j<10; j=j+1) begin
                    checks = checks + 1;
                    if (dut.fc_logits[j*32 +: 32] !== golden_fc[j]) begin
                        logits_mismatch = logits_mismatch + 1;
                        if (first_bad_stage == "NONE")
                            first_bad_stage = "FC";
                    end
                end
                fc_valid_count = fc_valid_count + 1;
            end

            if (dut.conv1_done) conv1_done_count = conv1_done_count+1;
            if (dut.conv2_done) conv2_done_count = conv2_done_count+1;
            if (dut.fc_done)    fc_done_count = fc_done_count+1;
            if (dut.argmax_valid) argmax_count = argmax_count+1;

            if (o_done !== o_valid)
                protocol_error = 1;
            if (o_done) begin
                done_count = done_count+1;
                if (first_done_cycle < 0) begin
                    first_done_cycle = global_cycle;
                    first_done_pixels = pixels_accepted;
                    prediction_at_done = o_digit;
                    if (pixels_accepted < INPUT_PIXELS)
                        protocol_error = 1;
                    if (o_busy !== 1'b0)
                        protocol_error = 1;
                end
            end
        end
    end

    task reset_dut;
        begin
            monitor_enable=0;
            @(negedge i_clk);
            i_rst_n=0;
            i_start=0;
            i_valid=0;
            i_pixel=0;
            repeat (5) @(posedge i_clk);
            @(negedge i_clk);
            i_rst_n=1;
            repeat (2) @(posedge i_clk);
            #3;
            if (o_busy !== 1'b0 || o_done !== 1'b0 ||
                dut.u_top_fsm.state !== 3'b000)
                $fatal(1,"FI_INFRA_ERROR: reset invariant failed");
        end
    endtask

    task start_frame;
        begin
            @(negedge i_clk);
            i_start=1;
            i_valid=0;
            @(posedge i_clk);
            #3;
            start_cycle=global_cycle;
            if (dut.u_top_fsm.state !== 3'b001 || o_busy !== 1'b1)
                $fatal(1,"FI_INFRA_ERROR: start not accepted");
            $display("START accepted @ cycle %0d", start_cycle);
            @(negedge i_clk);
            i_start=0;
            @(posedge i_clk);
            #3;
        end
    endtask

    task drive_pixels;
        integer k;
        begin
            for (k=0; k<INPUT_PIXELS; k=k+1) begin
                @(negedge i_clk);
                i_pixel=input_mem[k];
                i_valid=1;
                @(posedge i_clk);
                #1;
                pixels_driven=pixels_driven+1;
            end
            @(negedge i_clk);
            i_valid=0;
            i_pixel=0;
        end
    endtask

    task wait_deadline;
        begin
            while (global_cycle < (start_cycle+MAX_WAIT_CYCLES)) begin
                @(posedge i_clk);
                #3;
            end
        end
    endtask

    // The dispatch below is generated from fi_targets_candidates_v2.csv.
    // Each target has a STATIC elaborated hierarchy; target_id selects among
    // precompiled statements. No dynamic path strings and no force/release.
    task inject_once;
        begin
            fi_requested_cycle = start_cycle + fi_offset;
            if ((fi_offset < 3) || (fi_offset >= MAX_WAIT_CYCLES))
                $fatal(1,"FI_INFRA_ERROR: offset %0d outside [3,2999]",fi_offset);
            if (fi_requested_cycle <= global_cycle)
                $fatal(1,"FI_INFRA_ERROR: requested cycle already passed");

            while (global_cycle < fi_requested_cycle-1)
                @(negedge i_clk);
            #1;  // away from posedge and after the prior NBA update

            fi_before_word = 0;
            fi_after_word = 0;
            target_width = 0;
            target_class = "";

// @@DISPATCH_READ@@

            if (fi_bit < 0 || fi_bit >= target_width)
                $fatal(1,"FI_INFRA_ERROR: %0s bit %0d out of range [0,%0d]",
                    target_id,fi_bit,target_width-1);
            fi_old_bit = fi_before_word[fi_bit];
            if (fi_old_bit !== 1'b0 && fi_old_bit !== 1'b1)
                $fatal(1,"FI_INFRA_ERROR: %0s[%0d] is X/Z before injection",
                    target_id,fi_bit);
            fi_xor_mask = (320'b1 << fi_bit);

// @@DISPATCH_WRITE@@

            #1;
// @@DISPATCH_AFTER@@

            if ((fi_before_word ^ fi_after_word) !== fi_xor_mask)
                $fatal(1,"FI_INFRA_ERROR: not exactly one target bit flipped: %0s[%0d]",
                    target_id,fi_bit);

            fi_new_bit=fi_after_word[fi_bit];
            fi_events=fi_events+1;
            fi_injection_ok=1;
            $display("FI_APPLIED scenario=%0s target=%0s class=%0s bit=%0d old=%0b new=%0b requested=%0d",
                scenario_id,target_id,target_class,fi_bit,fi_old_bit,fi_new_bit,fi_requested_cycle);

            @(posedge i_clk);
            #3;
            fi_actual_cycle=global_cycle;
            if (fi_actual_cycle != fi_requested_cycle)
                $fatal(1,"FI_INFRA_ERROR: injection consumed cycle=%0d expected=%0d",
                    fi_actual_cycle,fi_requested_cycle);
        end
    endtask

    task classify_run;
        begin
            completed=(done_count>0);
            protocol_ok = (protocol_error == 0 &&
                pixels_driven == INPUT_PIXELS &&
                pixels_accepted == INPUT_PIXELS &&
                p1_words == POOL1_WORDS &&
                p2_words == POOL2_WORDS &&
                conv1_done_count == 1 &&
                conv2_done_count == 1 &&
                fc_done_count == 1 &&
                fc_valid_count == 1 &&
                argmax_count == 1 &&
                done_count == 1 &&
                first_done_pixels == INPUT_PIXELS);

            if (!completed) begin
                outcome=OUTCOME_NONCOMPLETION;
                if (first_bad_stage == "NONE") first_bad_stage="TIMEOUT";
            end
            else if (!protocol_ok) begin
                outcome=OUTCOME_PROTOCOL;
                if (first_bad_stage == "NONE") first_bad_stage="PROTOCOL";
            end
            else if (prediction_at_done != golden_pred[0][3:0]) begin
                outcome=OUTCOME_WRONG_LABEL;
                if (first_bad_stage == "NONE") first_bad_stage="ARGMAX";
            end
            else if (logits_mismatch != 0) begin
                outcome=OUTCOME_WRONG_LOGITS;
            end
            else begin
                outcome=OUTCOME_FINAL_MATCH;
            end
        end
    endtask

    task run_case;
        input integer enable_fault;
        begin
            fault_run=enable_fault;
            clear_stats();
            reset_dut();
            monitor_enable=1;
            start_frame();
            fork
                drive_pixels();
                begin
                    if (enable_fault) inject_once();
                end
                wait_deadline();
            join
            monitor_enable=0;
            classify_run();
            if (!enable_fault) begin
                if (outcome != OUTCOME_FINAL_MATCH ||
                    pool1_mismatch != 0 || pool2_mismatch != 0 ||
                    logits_mismatch != 0 ||
                    first_done_cycle-start_cycle != GOLDEN_LATENCY)
                    $fatal(1,"FI_DISABLED_EQUIVALENCE_FAIL: outcome=%0s latency=%0d p1err=%0d p2err=%0d fcerr=%0d",
                        outcome_name(outcome),first_done_cycle-start_cycle,
                        pool1_mismatch,pool2_mismatch,logits_mismatch);
                $display("FI_DISABLED_CONTROL_PASS latency=%0d pool1=%0d pool2=%0d logits=%0d prediction=%0d",
                    first_done_cycle-start_cycle,p1_words*4,p2_words*8,
                    fc_valid_count*10,prediction_at_done);
            end
        end
    endtask

    initial begin
        checks=0;
        errors=0;
        monitor_enable=0;
        global_cycle=0;
        fi_offset=125;
        fi_bit=2;
        scenario_id="FI_CTL_0005";
        sample_id="IMG0000";
        target_id="CTL_0001";
        input_file="D:/Share_Data_ubuntu_window/NKKH/data/input.hex";
        pool1_file="D:/Share_Data_ubuntu_window/NKKH/data/golden_pool1.hex";
        pool2_file="D:/Share_Data_ubuntu_window/NKKH/data/golden_pool2.hex";
        logits_file="D:/Share_Data_ubuntu_window/NKKH/data/golden.hex";
        prediction_file="D:/Share_Data_ubuntu_window/NKKH/data/prediction.hex";

        if ($value$plusargs("FI_OFFSET=%d",fi_offset)) begin end
        if ($value$plusargs("FI_BIT=%d",fi_bit)) begin end
        if ($value$plusargs("SCENARIO_ID=%s",scenario_id)) begin end
        if ($value$plusargs("SAMPLE_ID=%s",sample_id)) begin end
        if ($value$plusargs("TARGET_ID=%s",target_id)) begin end
        if ($value$plusargs("INPUT_FILE=%s",input_file)) begin end
        if ($value$plusargs("POOL1_FILE=%s",pool1_file)) begin end
        if ($value$plusargs("POOL2_FILE=%s",pool2_file)) begin end
        if ($value$plusargs("LOGITS_FILE=%s",logits_file)) begin end
        if ($value$plusargs("PREDICTION_FILE=%s",prediction_file)) begin end

        $display("=======================================================");
        $display("CNN_PEP FI CAMPAIGN V2 scenario=%0s target=%0s bit=%0d offset=%0d",
            scenario_id,target_id,fi_bit,fi_offset);
        $display("=======================================================");
        $readmemh(input_file,input_mem);
        $readmemh(pool1_file,golden_p1);
        $readmemh(pool2_file,golden_p2);
        $readmemh(logits_file,golden_fc);
        $readmemh(prediction_file,golden_pred);

        $display("CONTROL RUN: FI DISABLED");
        run_case(0);
        $display("FAULT RUN: %0s",scenario_id);
        run_case(1);

        if (fi_injection_ok != 1 || fi_events != 1)
            $fatal(1,"FI_INFRA_ERROR: expected one verified bit flip");

        $display("OUTCOME=%0s FIRST_BAD_STAGE=%0s",
            outcome_name(outcome),first_bad_stage);
        $display("FI_COUNTS driven=%0d accepted=%0d pool1words=%0d pool2words=%0d fcvalid=%0d argmax=%0d done=%0d",
            pixels_driven,pixels_accepted,p1_words,p2_words,
            fc_valid_count,argmax_count,done_count);
        // Stable schema. FI_RESULT_V2 is emitted once per valid scenario.
        $display("FI_RESULT_V2,%0s,%0s,%0s,%0s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0s,%0s",
            scenario_id,sample_id,target_id,target_class,
            fi_bit,fi_offset,fi_requested_cycle,fi_actual_cycle,
            fi_events,fi_injection_ok,first_done_cycle,first_done_pixels,
            pixels_driven,pixels_accepted,p1_words,p2_words,
            fc_valid_count,argmax_count,done_count,protocol_ok,
            pool1_mismatch,pool2_mismatch,logits_mismatch,
            prediction_at_done,first_bad_stage,outcome_name(outcome));
        $display("CNN_PEP FI CAMPAIGN V2: PASS (mechanism/log integrity; outcome may be harmful)");
        $finish;
    end
endmodule
`default_nettype wire
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--catalog', default='/mnt/data/fi_targets_candidates_v2.csv')
    ap.add_argument('--out', default='/mnt/data/tb_cnn_mnist_fi_campaign_v2.v')
    args = ap.parse_args()
    rows = list(csv.DictReader(Path(args.catalog).open(newline='', encoding='utf-8')))
    ids = [r['target_id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate target ID')
    supported = []
    for r in rows:
        path = r['hier_signal'].strip()
        width = int(r['width'])
        # restrict generated hierarchical paths to conventional RTL identifiers / constant indexes
        import re
        if not re.fullmatch(r'dut(?:\.[A-Za-z_][A-Za-z_0-9]*(?:\[\d+\])?)+', path):
            raise ValueError(f'bad RTL hierarchy path: {path}')
        if not (1 <= width <= 320):
            raise ValueError(f'width out of range {r}')
        supported.append((r['target_id'], r['class'], path, width))

    pre, write, after = [], [], []
    pre.append('            case (target_id)')
    write.append('            case (target_id)')
    after.append('            case (target_id)')
    for tid, cls, path, width in supported:
        pre.append(f'                "{tid}": begin')
        pre.append(f'                    target_width={width}; target_class="{cls}";')
        read_value = path if width == 320 else f'{{{{{320-width}{{1\'b0}}}}, {path}}}'
        pre.append(f'                    fi_before_word={read_value};')
        pre.append('                end')
        if width == 1:
            action = f'{path} = ~{path};'
        else:
            action = f'{path}[fi_bit] = ~{path}[fi_bit];'
        write.append(f'                "{tid}": {action}')
        after.append(f'                "{tid}": fi_after_word={read_value};')
    pre.append('                default: $fatal(1,"FI_INFRA_ERROR: unsupported target_id=%0s",target_id);')
    pre.append('            endcase')
    write.append('                default: $fatal(1,"FI_INFRA_ERROR: target mismatch");')
    write.append('            endcase')
    after.append('                default: $fatal(1,"FI_INFRA_ERROR: after-read target mismatch");')
    after.append('            endcase')
    out = (HEAD.replace('// @@DISPATCH_READ@@', '\n'.join(pre))
               .replace('// @@DISPATCH_WRITE@@', '\n'.join(write))
               .replace('// @@DISPATCH_AFTER@@', '\n'.join(after)))
    Path(args.out).write_text(out,encoding='utf-8')
    print(f'Generated {args.out} ({len(supported)} static targets, {len(out.splitlines())} lines)')


if __name__ == '__main__':
    main()
