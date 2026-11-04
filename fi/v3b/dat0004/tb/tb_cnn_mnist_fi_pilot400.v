`timescale 1ns / 1ps
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
// NOTE: This new pilot TB must be compiled/verified in XSim. Frozen P0 RTL unchanged.
// Verify deposit semantics on simulator waveforms for each target class.
//
// Plusargs:
//   FI_OFFSET=125 TARGET_ID=CTL_0001 FI_BIT=2 SCENARIO_ID=FI_CTL_0005
//   SAMPLE_ID=IMG0000 INPUT_FILE=<path> POOL1_FILE=<path>
//   POOL2_FILE=<path> LOGITS_FILE=<path> PREDICTION_FILE=<path>
//
// TB top: tb_cnn_mnist_fi_pilot400
// ============================================================================

module tb_cnn_mnist_fi_pilot400;
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
    integer pr_mem1_use_index, pr_mem2_use_index, pr_conv2_use_index;
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

    // Preserve the exact FC logits sampled by ArgMax in the PRE-NBA region.
    // The existing post-NBA fc_valid checker runs at the FC produce edge and
    // therefore cannot see a deposit made between produce and consume edges.
    reg argmax_input_pre_valid;
    reg [319:0] argmax_input_pre_logits;
    reg [9:0] logits_bad_seen;
    integer argmax_input_diff;

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
            logits_bad_seen=10'd0;
            argmax_input_pre_valid=0;
            argmax_input_pre_logits=320'd0;
            argmax_input_diff=0;
            first_bad_stage="NONE";
            prediction_at_done=-1;
            fi_requested_cycle=-1;
            fi_actual_cycle=-1;
            fi_events=0;
            fi_injection_ok=0;
            pr_mem1_use_index=0;
            pr_mem2_use_index=0;
            pr_conv2_use_index=0;
            outcome=0;
            protocol_ok=0;
            completed=0;
        end
    endtask

    // Count input acceptance before DUT NBAs, then sample DUT outputs after
    // NBAs (#2). This avoids confusing a newly asserted o_valid with a
    // transaction consumed at the same edge.
    always @(posedge i_clk) begin
        // Snapshot data *before* the sequential ArgMax always block samples
        // and before FC can deassert o_valid in the NBA region.
        argmax_input_pre_valid = i_rst_n && monitor_enable &&
                                 dut.u_argmax.i_valid;
        if (argmax_input_pre_valid)
            argmax_input_pre_logits = dut.u_argmax.i_logits;

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
                        // A class is counted only once even if it is wrong at
                        // both FC production and ArgMax consumption.
                        if (!logits_bad_seen[j]) begin
                            logits_bad_seen[j] = 1'b1;
                            logits_mismatch = logits_mismatch + 1;
                            if (first_bad_stage == "NONE")
                                first_bad_stage = "FC";
                        end
                    end
                end
                fc_valid_count = fc_valid_count + 1;
            end

            // Compare the PRE-NBA consumer snapshot, not the post-NBA FC
            // output. This catches late single-bit upsets in registered
            // logits between the FC production edge and ArgMax sampling.
            if (argmax_input_pre_valid) begin
                argmax_input_diff = 0;
                for (j=0; j<10; j=j+1) begin
                    if (argmax_input_pre_logits[j*32 +: 32] !== golden_fc[j]) begin
                        argmax_input_diff = argmax_input_diff + 1;
                        if (!logits_bad_seen[j]) begin
                            logits_bad_seen[j] = 1'b1;
                            logits_mismatch = logits_mismatch + 1;
                            if (first_bad_stage == "NONE")
                                first_bad_stage = "FC";
                        end
                    end
                end
                if (fault_run && (target_id == "DAT_0010"))
                    $display("FI_DAT_ARGMAX_CONSUME cycle=%0d logit0=%08h golden0=%08h mismatched_classes=%0d",
                        global_cycle,argmax_input_pre_logits[31:0],golden_fc[0],
                        argmax_input_diff);
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

            case (target_id)
                "CTL_0001": begin
                    target_width=3; target_class="CTL";
                    fi_before_word={{317{1'b0}}, dut.u_top_fsm.state};
                end
                "CTL_0002": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_top_fsm.o_start_conv1};
                end
                "CTL_0003": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_top_fsm.o_start_rd1};
                end
                "CTL_0004": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_top_fsm.o_start_conv2};
                end
                "CTL_0005": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_top_fsm.o_start_rd2};
                end
                "CTL_0006": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_top_fsm.o_start_fc};
                end
                "CTL_0007": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_top_fsm.o_busy};
                end
                "CTL_0008": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_top_fsm.o_valid};
                end
                "CTL_0009": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_top_fsm.o_done};
                end
                "CTL_0010": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_conv1_layer.r_busy};
                end
                "CTL_0011": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_conv2_layer.r_busy};
                end
                "CTL_0012": begin
                    target_width=8; target_class="CTL";
                    fi_before_word={{312{1'b0}}, dut.u_feature_mem1.wr_ptr};
                end
                "CTL_0013": begin
                    target_width=8; target_class="CTL";
                    fi_before_word={{312{1'b0}}, dut.u_feature_mem1.rd_ptr};
                end
                "CTL_0014": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_feature_mem1.rd_active};
                end
                "CTL_0015": begin
                    target_width=5; target_class="CTL";
                    fi_before_word={{315{1'b0}}, dut.u_feature_mem2.wr_addr};
                end
                "CTL_0016": begin
                    target_width=5; target_class="CTL";
                    fi_before_word={{315{1'b0}}, dut.u_feature_mem2.rd_addr};
                end
                "CTL_0017": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_feature_mem2.wr_full};
                end
                "CTL_0018": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_feature_mem2.rd_active};
                end
                "CTL_0019": begin
                    target_width=5; target_class="CTL";
                    fi_before_word={{315{1'b0}}, dut.u_fully_connected.cycle_count};
                end
                "CTL_0020": begin
                    target_width=1; target_class="CTL";
                    fi_before_word={{319{1'b0}}, dut.u_fully_connected.r_busy};
                end
                "CTL_0021": begin
                    target_width=2; target_class="CTL";
                    fi_before_word={{318{1'b0}}, dut.u_conv1_layer.u_pool1_reorder.state};
                end
                "CTL_0022": begin
                    target_width=2; target_class="CTL";
                    fi_before_word={{318{1'b0}}, dut.u_conv2_layer.u_pool2_buf.state};
                end
                "CTL_0023": begin
                    target_width=2; target_class="CTL";
                    fi_before_word={{318{1'b0}}, dut.u_conv2_layer.u_pool2_reorder.state};
                end
                "CTL_0024": begin
                    target_width=5; target_class="CTL";
                    fi_before_word={{315{1'b0}}, dut.u_conv1_layer.u_pool1_buf.x_count};
                end
                "CTL_0025": begin
                    target_width=5; target_class="CTL";
                    fi_before_word={{315{1'b0}}, dut.u_conv1_layer.u_pool1_buf.y_count};
                end
                "BUF_0001": begin
                    target_width=32; target_class="BUF";
                    fi_before_word={{288{1'b0}}, dut.u_conv1_layer.u_pool1_buf.line_buf[0]};
                end
                "BUF_0002": begin
                    target_width=32; target_class="BUF";
                    fi_before_word={{288{1'b0}}, dut.u_conv1_layer.u_pool1_buf.line_buf[13]};
                end
                "BUF_0003": begin
                    target_width=32; target_class="BUF";
                    fi_before_word={{288{1'b0}}, dut.u_conv1_layer.u_pool1_buf.line_buf[25]};
                end
                "BUF_0004": begin
                    target_width=32; target_class="BUF";
                    fi_before_word={{288{1'b0}}, dut.u_conv1_layer.u_pool1_buf.prev_top};
                end
                "BUF_0005": begin
                    target_width=32; target_class="BUF";
                    fi_before_word={{288{1'b0}}, dut.u_conv1_layer.u_pool1_buf.prev_curr};
                end
                "BUF_0006": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_pool1_reorder.reorder_mem[0]};
                end
                "BUF_0007": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_pool1_reorder.reorder_mem[337]};
                end
                "BUF_0008": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_pool1_reorder.reorder_mem[675]};
                end
                "BUF_0009": begin
                    target_width=64; target_class="BUF";
                    fi_before_word={{256{1'b0}}, dut.u_conv2_layer.u_pool2_buf.spatial_mem[0]};
                end
                "BUF_0010": begin
                    target_width=64; target_class="BUF";
                    fi_before_word={{256{1'b0}}, dut.u_conv2_layer.u_pool2_buf.spatial_mem[60]};
                end
                "BUF_0011": begin
                    target_width=64; target_class="BUF";
                    fi_before_word={{256{1'b0}}, dut.u_conv2_layer.u_pool2_buf.spatial_mem[120]};
                end
                "BUF_0012": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv2_layer.u_pool2_reorder.reorder_mem[0]};
                end
                "BUF_0013": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv2_layer.u_pool2_reorder.reorder_mem[100]};
                end
                "BUF_0014": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv2_layer.u_pool2_reorder.reorder_mem[199]};
                end
                "BUF_0015": begin
                    target_width=128; target_class="BUF";
                    fi_before_word={{192{1'b0}}, dut.u_conv1_layer.u_pool1_buf.o_window};
                end
                "BUF_0016": begin
                    target_width=256; target_class="BUF";
                    fi_before_word={{64{1'b0}}, dut.u_conv2_layer.u_pool2_buf.o_window};
                end
                "MEM_0001": begin
                    target_width=32; target_class="MEM";
                    fi_before_word={{288{1'b0}}, dut.u_feature_mem1.mem[0]};
                end
                "MEM_0002": begin
                    target_width=32; target_class="MEM";
                    fi_before_word={{288{1'b0}}, dut.u_feature_mem1.mem[84]};
                end
                "MEM_0003": begin
                    target_width=32; target_class="MEM";
                    fi_before_word={{288{1'b0}}, dut.u_feature_mem1.mem[168]};
                end
                "MEM_0004": begin
                    target_width=64; target_class="MEM";
                    fi_before_word={{256{1'b0}}, dut.u_feature_mem2.mem[0]};
                end
                "MEM_0005": begin
                    target_width=64; target_class="MEM";
                    fi_before_word={{256{1'b0}}, dut.u_feature_mem2.mem[12]};
                end
                "MEM_0006": begin
                    target_width=64; target_class="MEM";
                    fi_before_word={{256{1'b0}}, dut.u_feature_mem2.mem[24]};
                end
                "DAT_0001": begin
                    target_width=128; target_class="DAT";
                    fi_before_word={{192{1'b0}}, dut.u_conv1_layer.u_conv1_calc.o_acc};
                end
                "DAT_0002": begin
                    target_width=32; target_class="DAT";
                    fi_before_word={{288{1'b0}}, dut.u_conv1_layer.u_conv1_quant_relu.o_data};
                end
                "DAT_0003": begin
                    target_width=32; target_class="DAT";
                    fi_before_word={{288{1'b0}}, dut.u_conv1_layer.u_pool1.o_data};
                end
                "DAT_0004": begin
                    target_width=256; target_class="DAT";
                    fi_before_word={{64{1'b0}}, dut.u_conv2_layer.u_conv2_calc.o_acc};
                end
                "DAT_0005": begin
                    target_width=64; target_class="DAT";
                    fi_before_word={{256{1'b0}}, dut.u_conv2_layer.u_conv2_quant_relu.o_data};
                end
                "DAT_0006": begin
                    target_width=64; target_class="DAT";
                    fi_before_word={{256{1'b0}}, dut.u_conv2_layer.u_pool2.o_data};
                end
                "DAT_0007": begin
                    target_width=32; target_class="DAT";
                    fi_before_word={{288{1'b0}}, dut.u_fully_connected.accumulator[0]};
                end
                "DAT_0008": begin
                    target_width=32; target_class="DAT";
                    fi_before_word={{288{1'b0}}, dut.u_fully_connected.accumulator[5]};
                end
                "DAT_0009": begin
                    target_width=32; target_class="DAT";
                    fi_before_word={{288{1'b0}}, dut.u_fully_connected.accumulator[9]};
                end
                "DAT_0010": begin
                    target_width=320; target_class="DAT";
                    fi_before_word=dut.u_fully_connected.o_logits;
                end
                "CTL_0026": begin
                    target_width=5; target_class="CTL";
                    fi_before_word={{315{1'b0}}, dut.u_conv1_layer.u_conv1_buf.x_count};
                end
                "CTL_0027": begin
                    target_width=5; target_class="CTL";
                    fi_before_word={{315{1'b0}}, dut.u_conv1_layer.u_conv1_buf.y_count};
                end
                "CTL_0028": begin
                    target_width=2; target_class="CTL";
                    fi_before_word={{318{1'b0}}, dut.u_conv2_layer.u_conv2_buf.state};
                end
                "CTL_0029": begin
                    target_width=8; target_class="CTL";
                    fi_before_word={{312{1'b0}}, dut.u_conv2_layer.u_conv2_buf.capture_word_count};
                end
                "CTL_0030": begin
                    target_width=4; target_class="CTL";
                    fi_before_word={{316{1'b0}}, dut.u_conv2_layer.u_conv2_buf.out_y};
                end
                "CTL_0031": begin
                    target_width=4; target_class="CTL";
                    fi_before_word={{316{1'b0}}, dut.u_conv2_layer.u_conv2_buf.out_x};
                end
                "BUF_0017": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.line_buf1[0]};
                end
                "BUF_0018": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.line_buf1[14]};
                end
                "BUF_0019": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.line_buf1[27]};
                end
                "BUF_0020": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.line_buf2[0]};
                end
                "BUF_0021": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.line_buf2[14]};
                end
                "BUF_0022": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.line_buf2[27]};
                end
                "BUF_0023": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.top_d1};
                end
                "BUF_0024": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.top_d2};
                end
                "BUF_0025": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.mid_d1};
                end
                "BUF_0026": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.mid_d2};
                end
                "BUF_0027": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.bot_d1};
                end
                "BUF_0028": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.bot_d2};
                end
                "BUF_0029": begin
                    target_width=72; target_class="BUF";
                    fi_before_word={{248{1'b0}}, dut.u_conv1_layer.u_conv1_buf.o_window};
                end
                "BUF_0030": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv2_layer.u_conv2_buf.feature_mem[0]};
                end
                "BUF_0031": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv2_layer.u_conv2_buf.feature_mem[337]};
                end
                "BUF_0032": begin
                    target_width=8; target_class="BUF";
                    fi_before_word={{312{1'b0}}, dut.u_conv2_layer.u_conv2_buf.feature_mem[675]};
                end
                "BUF_0033": begin
                    target_width=288; target_class="BUF";
                    fi_before_word={{32{1'b0}}, dut.u_conv2_layer.u_conv2_buf.o_window};
                end
                default: $fatal(1,"FI_INFRA_ERROR: unsupported target_id=%0s",target_id);
            endcase

            if (fi_bit < 0 || fi_bit >= target_width)
                $fatal(1,"FI_INFRA_ERROR: %0s bit %0d out of range [0,%0d]",
                    target_id,fi_bit,target_width-1);
            fi_old_bit = fi_before_word[fi_bit];
            if (fi_old_bit !== 1'b0 && fi_old_bit !== 1'b1)
                $fatal(1,"FI_INFRA_ERROR: %0s[%0d] is X/Z before injection",
                    target_id,fi_bit);
            fi_xor_mask = (320'b1 << fi_bit);

            case (target_id)
                "CTL_0001": dut.u_top_fsm.state[fi_bit] = ~dut.u_top_fsm.state[fi_bit];
                "CTL_0002": dut.u_top_fsm.o_start_conv1 = ~dut.u_top_fsm.o_start_conv1;
                "CTL_0003": dut.u_top_fsm.o_start_rd1 = ~dut.u_top_fsm.o_start_rd1;
                "CTL_0004": dut.u_top_fsm.o_start_conv2 = ~dut.u_top_fsm.o_start_conv2;
                "CTL_0005": dut.u_top_fsm.o_start_rd2 = ~dut.u_top_fsm.o_start_rd2;
                "CTL_0006": dut.u_top_fsm.o_start_fc = ~dut.u_top_fsm.o_start_fc;
                "CTL_0007": dut.u_top_fsm.o_busy = ~dut.u_top_fsm.o_busy;
                "CTL_0008": dut.u_top_fsm.o_valid = ~dut.u_top_fsm.o_valid;
                "CTL_0009": dut.u_top_fsm.o_done = ~dut.u_top_fsm.o_done;
                "CTL_0010": dut.u_conv1_layer.r_busy = ~dut.u_conv1_layer.r_busy;
                "CTL_0011": dut.u_conv2_layer.r_busy = ~dut.u_conv2_layer.r_busy;
                "CTL_0012": dut.u_feature_mem1.wr_ptr[fi_bit] = ~dut.u_feature_mem1.wr_ptr[fi_bit];
                "CTL_0013": dut.u_feature_mem1.rd_ptr[fi_bit] = ~dut.u_feature_mem1.rd_ptr[fi_bit];
                "CTL_0014": dut.u_feature_mem1.rd_active = ~dut.u_feature_mem1.rd_active;
                "CTL_0015": dut.u_feature_mem2.wr_addr[fi_bit] = ~dut.u_feature_mem2.wr_addr[fi_bit];
                "CTL_0016": dut.u_feature_mem2.rd_addr[fi_bit] = ~dut.u_feature_mem2.rd_addr[fi_bit];
                "CTL_0017": dut.u_feature_mem2.wr_full = ~dut.u_feature_mem2.wr_full;
                "CTL_0018": dut.u_feature_mem2.rd_active = ~dut.u_feature_mem2.rd_active;
                "CTL_0019": dut.u_fully_connected.cycle_count[fi_bit] = ~dut.u_fully_connected.cycle_count[fi_bit];
                "CTL_0020": dut.u_fully_connected.r_busy = ~dut.u_fully_connected.r_busy;
                "CTL_0021": dut.u_conv1_layer.u_pool1_reorder.state[fi_bit] = ~dut.u_conv1_layer.u_pool1_reorder.state[fi_bit];
                "CTL_0022": dut.u_conv2_layer.u_pool2_buf.state[fi_bit] = ~dut.u_conv2_layer.u_pool2_buf.state[fi_bit];
                "CTL_0023": dut.u_conv2_layer.u_pool2_reorder.state[fi_bit] = ~dut.u_conv2_layer.u_pool2_reorder.state[fi_bit];
                "CTL_0024": dut.u_conv1_layer.u_pool1_buf.x_count[fi_bit] = ~dut.u_conv1_layer.u_pool1_buf.x_count[fi_bit];
                "CTL_0025": dut.u_conv1_layer.u_pool1_buf.y_count[fi_bit] = ~dut.u_conv1_layer.u_pool1_buf.y_count[fi_bit];
                "BUF_0001": dut.u_conv1_layer.u_pool1_buf.line_buf[0][fi_bit] = ~dut.u_conv1_layer.u_pool1_buf.line_buf[0][fi_bit];
                "BUF_0002": dut.u_conv1_layer.u_pool1_buf.line_buf[13][fi_bit] = ~dut.u_conv1_layer.u_pool1_buf.line_buf[13][fi_bit];
                "BUF_0003": dut.u_conv1_layer.u_pool1_buf.line_buf[25][fi_bit] = ~dut.u_conv1_layer.u_pool1_buf.line_buf[25][fi_bit];
                "BUF_0004": dut.u_conv1_layer.u_pool1_buf.prev_top[fi_bit] = ~dut.u_conv1_layer.u_pool1_buf.prev_top[fi_bit];
                "BUF_0005": dut.u_conv1_layer.u_pool1_buf.prev_curr[fi_bit] = ~dut.u_conv1_layer.u_pool1_buf.prev_curr[fi_bit];
                "BUF_0006": dut.u_conv1_layer.u_pool1_reorder.reorder_mem[0][fi_bit] = ~dut.u_conv1_layer.u_pool1_reorder.reorder_mem[0][fi_bit];
                "BUF_0007": dut.u_conv1_layer.u_pool1_reorder.reorder_mem[337][fi_bit] = ~dut.u_conv1_layer.u_pool1_reorder.reorder_mem[337][fi_bit];
                "BUF_0008": dut.u_conv1_layer.u_pool1_reorder.reorder_mem[675][fi_bit] = ~dut.u_conv1_layer.u_pool1_reorder.reorder_mem[675][fi_bit];
                "BUF_0009": dut.u_conv2_layer.u_pool2_buf.spatial_mem[0][fi_bit] = ~dut.u_conv2_layer.u_pool2_buf.spatial_mem[0][fi_bit];
                "BUF_0010": dut.u_conv2_layer.u_pool2_buf.spatial_mem[60][fi_bit] = ~dut.u_conv2_layer.u_pool2_buf.spatial_mem[60][fi_bit];
                "BUF_0011": dut.u_conv2_layer.u_pool2_buf.spatial_mem[120][fi_bit] = ~dut.u_conv2_layer.u_pool2_buf.spatial_mem[120][fi_bit];
                "BUF_0012": dut.u_conv2_layer.u_pool2_reorder.reorder_mem[0][fi_bit] = ~dut.u_conv2_layer.u_pool2_reorder.reorder_mem[0][fi_bit];
                "BUF_0013": dut.u_conv2_layer.u_pool2_reorder.reorder_mem[100][fi_bit] = ~dut.u_conv2_layer.u_pool2_reorder.reorder_mem[100][fi_bit];
                "BUF_0014": dut.u_conv2_layer.u_pool2_reorder.reorder_mem[199][fi_bit] = ~dut.u_conv2_layer.u_pool2_reorder.reorder_mem[199][fi_bit];
                "BUF_0015": dut.u_conv1_layer.u_pool1_buf.o_window[fi_bit] = ~dut.u_conv1_layer.u_pool1_buf.o_window[fi_bit];
                "BUF_0016": dut.u_conv2_layer.u_pool2_buf.o_window[fi_bit] = ~dut.u_conv2_layer.u_pool2_buf.o_window[fi_bit];
                "MEM_0001": dut.u_feature_mem1.mem[0][fi_bit] = ~dut.u_feature_mem1.mem[0][fi_bit];
                "MEM_0002": dut.u_feature_mem1.mem[84][fi_bit] = ~dut.u_feature_mem1.mem[84][fi_bit];
                "MEM_0003": dut.u_feature_mem1.mem[168][fi_bit] = ~dut.u_feature_mem1.mem[168][fi_bit];
                "MEM_0004": dut.u_feature_mem2.mem[0][fi_bit] = ~dut.u_feature_mem2.mem[0][fi_bit];
                "MEM_0005": dut.u_feature_mem2.mem[12][fi_bit] = ~dut.u_feature_mem2.mem[12][fi_bit];
                "MEM_0006": dut.u_feature_mem2.mem[24][fi_bit] = ~dut.u_feature_mem2.mem[24][fi_bit];
                "DAT_0001": dut.u_conv1_layer.u_conv1_calc.o_acc[fi_bit] = ~dut.u_conv1_layer.u_conv1_calc.o_acc[fi_bit];
                "DAT_0002": dut.u_conv1_layer.u_conv1_quant_relu.o_data[fi_bit] = ~dut.u_conv1_layer.u_conv1_quant_relu.o_data[fi_bit];
                "DAT_0003": dut.u_conv1_layer.u_pool1.o_data[fi_bit] = ~dut.u_conv1_layer.u_pool1.o_data[fi_bit];
                "DAT_0004": dut.u_conv2_layer.u_conv2_calc.o_acc[fi_bit] = ~dut.u_conv2_layer.u_conv2_calc.o_acc[fi_bit];
                "DAT_0005": dut.u_conv2_layer.u_conv2_quant_relu.o_data[fi_bit] = ~dut.u_conv2_layer.u_conv2_quant_relu.o_data[fi_bit];
                "DAT_0006": dut.u_conv2_layer.u_pool2.o_data[fi_bit] = ~dut.u_conv2_layer.u_pool2.o_data[fi_bit];
                "DAT_0007": dut.u_fully_connected.accumulator[0][fi_bit] = ~dut.u_fully_connected.accumulator[0][fi_bit];
                "DAT_0008": dut.u_fully_connected.accumulator[5][fi_bit] = ~dut.u_fully_connected.accumulator[5][fi_bit];
                "DAT_0009": dut.u_fully_connected.accumulator[9][fi_bit] = ~dut.u_fully_connected.accumulator[9][fi_bit];
                "DAT_0010": dut.u_fully_connected.o_logits[fi_bit] = ~dut.u_fully_connected.o_logits[fi_bit];
                "CTL_0026": dut.u_conv1_layer.u_conv1_buf.x_count[fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.x_count[fi_bit];
                "CTL_0027": dut.u_conv1_layer.u_conv1_buf.y_count[fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.y_count[fi_bit];
                "CTL_0028": dut.u_conv2_layer.u_conv2_buf.state[fi_bit] = ~dut.u_conv2_layer.u_conv2_buf.state[fi_bit];
                "CTL_0029": dut.u_conv2_layer.u_conv2_buf.capture_word_count[fi_bit] = ~dut.u_conv2_layer.u_conv2_buf.capture_word_count[fi_bit];
                "CTL_0030": dut.u_conv2_layer.u_conv2_buf.out_y[fi_bit] = ~dut.u_conv2_layer.u_conv2_buf.out_y[fi_bit];
                "CTL_0031": dut.u_conv2_layer.u_conv2_buf.out_x[fi_bit] = ~dut.u_conv2_layer.u_conv2_buf.out_x[fi_bit];
                "BUF_0017": dut.u_conv1_layer.u_conv1_buf.line_buf1[0][fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.line_buf1[0][fi_bit];
                "BUF_0018": dut.u_conv1_layer.u_conv1_buf.line_buf1[14][fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.line_buf1[14][fi_bit];
                "BUF_0019": dut.u_conv1_layer.u_conv1_buf.line_buf1[27][fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.line_buf1[27][fi_bit];
                "BUF_0020": dut.u_conv1_layer.u_conv1_buf.line_buf2[0][fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.line_buf2[0][fi_bit];
                "BUF_0021": dut.u_conv1_layer.u_conv1_buf.line_buf2[14][fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.line_buf2[14][fi_bit];
                "BUF_0022": dut.u_conv1_layer.u_conv1_buf.line_buf2[27][fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.line_buf2[27][fi_bit];
                "BUF_0023": dut.u_conv1_layer.u_conv1_buf.top_d1[fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.top_d1[fi_bit];
                "BUF_0024": dut.u_conv1_layer.u_conv1_buf.top_d2[fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.top_d2[fi_bit];
                "BUF_0025": dut.u_conv1_layer.u_conv1_buf.mid_d1[fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.mid_d1[fi_bit];
                "BUF_0026": dut.u_conv1_layer.u_conv1_buf.mid_d2[fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.mid_d2[fi_bit];
                "BUF_0027": dut.u_conv1_layer.u_conv1_buf.bot_d1[fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.bot_d1[fi_bit];
                "BUF_0028": dut.u_conv1_layer.u_conv1_buf.bot_d2[fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.bot_d2[fi_bit];
                "BUF_0029": dut.u_conv1_layer.u_conv1_buf.o_window[fi_bit] = ~dut.u_conv1_layer.u_conv1_buf.o_window[fi_bit];
                "BUF_0030": dut.u_conv2_layer.u_conv2_buf.feature_mem[0][fi_bit] = ~dut.u_conv2_layer.u_conv2_buf.feature_mem[0][fi_bit];
                "BUF_0031": dut.u_conv2_layer.u_conv2_buf.feature_mem[337][fi_bit] = ~dut.u_conv2_layer.u_conv2_buf.feature_mem[337][fi_bit];
                "BUF_0032": dut.u_conv2_layer.u_conv2_buf.feature_mem[675][fi_bit] = ~dut.u_conv2_layer.u_conv2_buf.feature_mem[675][fi_bit];
                "BUF_0033": dut.u_conv2_layer.u_conv2_buf.o_window[fi_bit] = ~dut.u_conv2_layer.u_conv2_buf.o_window[fi_bit];
                default: $fatal(1,"FI_INFRA_ERROR: target mismatch");
            endcase

            #1;
            case (target_id)
                "CTL_0001": fi_after_word={{317{1'b0}}, dut.u_top_fsm.state};
                "CTL_0002": fi_after_word={{319{1'b0}}, dut.u_top_fsm.o_start_conv1};
                "CTL_0003": fi_after_word={{319{1'b0}}, dut.u_top_fsm.o_start_rd1};
                "CTL_0004": fi_after_word={{319{1'b0}}, dut.u_top_fsm.o_start_conv2};
                "CTL_0005": fi_after_word={{319{1'b0}}, dut.u_top_fsm.o_start_rd2};
                "CTL_0006": fi_after_word={{319{1'b0}}, dut.u_top_fsm.o_start_fc};
                "CTL_0007": fi_after_word={{319{1'b0}}, dut.u_top_fsm.o_busy};
                "CTL_0008": fi_after_word={{319{1'b0}}, dut.u_top_fsm.o_valid};
                "CTL_0009": fi_after_word={{319{1'b0}}, dut.u_top_fsm.o_done};
                "CTL_0010": fi_after_word={{319{1'b0}}, dut.u_conv1_layer.r_busy};
                "CTL_0011": fi_after_word={{319{1'b0}}, dut.u_conv2_layer.r_busy};
                "CTL_0012": fi_after_word={{312{1'b0}}, dut.u_feature_mem1.wr_ptr};
                "CTL_0013": fi_after_word={{312{1'b0}}, dut.u_feature_mem1.rd_ptr};
                "CTL_0014": fi_after_word={{319{1'b0}}, dut.u_feature_mem1.rd_active};
                "CTL_0015": fi_after_word={{315{1'b0}}, dut.u_feature_mem2.wr_addr};
                "CTL_0016": fi_after_word={{315{1'b0}}, dut.u_feature_mem2.rd_addr};
                "CTL_0017": fi_after_word={{319{1'b0}}, dut.u_feature_mem2.wr_full};
                "CTL_0018": fi_after_word={{319{1'b0}}, dut.u_feature_mem2.rd_active};
                "CTL_0019": fi_after_word={{315{1'b0}}, dut.u_fully_connected.cycle_count};
                "CTL_0020": fi_after_word={{319{1'b0}}, dut.u_fully_connected.r_busy};
                "CTL_0021": fi_after_word={{318{1'b0}}, dut.u_conv1_layer.u_pool1_reorder.state};
                "CTL_0022": fi_after_word={{318{1'b0}}, dut.u_conv2_layer.u_pool2_buf.state};
                "CTL_0023": fi_after_word={{318{1'b0}}, dut.u_conv2_layer.u_pool2_reorder.state};
                "CTL_0024": fi_after_word={{315{1'b0}}, dut.u_conv1_layer.u_pool1_buf.x_count};
                "CTL_0025": fi_after_word={{315{1'b0}}, dut.u_conv1_layer.u_pool1_buf.y_count};
                "BUF_0001": fi_after_word={{288{1'b0}}, dut.u_conv1_layer.u_pool1_buf.line_buf[0]};
                "BUF_0002": fi_after_word={{288{1'b0}}, dut.u_conv1_layer.u_pool1_buf.line_buf[13]};
                "BUF_0003": fi_after_word={{288{1'b0}}, dut.u_conv1_layer.u_pool1_buf.line_buf[25]};
                "BUF_0004": fi_after_word={{288{1'b0}}, dut.u_conv1_layer.u_pool1_buf.prev_top};
                "BUF_0005": fi_after_word={{288{1'b0}}, dut.u_conv1_layer.u_pool1_buf.prev_curr};
                "BUF_0006": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_pool1_reorder.reorder_mem[0]};
                "BUF_0007": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_pool1_reorder.reorder_mem[337]};
                "BUF_0008": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_pool1_reorder.reorder_mem[675]};
                "BUF_0009": fi_after_word={{256{1'b0}}, dut.u_conv2_layer.u_pool2_buf.spatial_mem[0]};
                "BUF_0010": fi_after_word={{256{1'b0}}, dut.u_conv2_layer.u_pool2_buf.spatial_mem[60]};
                "BUF_0011": fi_after_word={{256{1'b0}}, dut.u_conv2_layer.u_pool2_buf.spatial_mem[120]};
                "BUF_0012": fi_after_word={{312{1'b0}}, dut.u_conv2_layer.u_pool2_reorder.reorder_mem[0]};
                "BUF_0013": fi_after_word={{312{1'b0}}, dut.u_conv2_layer.u_pool2_reorder.reorder_mem[100]};
                "BUF_0014": fi_after_word={{312{1'b0}}, dut.u_conv2_layer.u_pool2_reorder.reorder_mem[199]};
                "BUF_0015": fi_after_word={{192{1'b0}}, dut.u_conv1_layer.u_pool1_buf.o_window};
                "BUF_0016": fi_after_word={{64{1'b0}}, dut.u_conv2_layer.u_pool2_buf.o_window};
                "MEM_0001": fi_after_word={{288{1'b0}}, dut.u_feature_mem1.mem[0]};
                "MEM_0002": fi_after_word={{288{1'b0}}, dut.u_feature_mem1.mem[84]};
                "MEM_0003": fi_after_word={{288{1'b0}}, dut.u_feature_mem1.mem[168]};
                "MEM_0004": fi_after_word={{256{1'b0}}, dut.u_feature_mem2.mem[0]};
                "MEM_0005": fi_after_word={{256{1'b0}}, dut.u_feature_mem2.mem[12]};
                "MEM_0006": fi_after_word={{256{1'b0}}, dut.u_feature_mem2.mem[24]};
                "DAT_0001": fi_after_word={{192{1'b0}}, dut.u_conv1_layer.u_conv1_calc.o_acc};
                "DAT_0002": fi_after_word={{288{1'b0}}, dut.u_conv1_layer.u_conv1_quant_relu.o_data};
                "DAT_0003": fi_after_word={{288{1'b0}}, dut.u_conv1_layer.u_pool1.o_data};
                "DAT_0004": fi_after_word={{64{1'b0}}, dut.u_conv2_layer.u_conv2_calc.o_acc};
                "DAT_0005": fi_after_word={{256{1'b0}}, dut.u_conv2_layer.u_conv2_quant_relu.o_data};
                "DAT_0006": fi_after_word={{256{1'b0}}, dut.u_conv2_layer.u_pool2.o_data};
                "DAT_0007": fi_after_word={{288{1'b0}}, dut.u_fully_connected.accumulator[0]};
                "DAT_0008": fi_after_word={{288{1'b0}}, dut.u_fully_connected.accumulator[5]};
                "DAT_0009": fi_after_word={{288{1'b0}}, dut.u_fully_connected.accumulator[9]};
                "DAT_0010": fi_after_word=dut.u_fully_connected.o_logits;
                "CTL_0026": fi_after_word={{315{1'b0}}, dut.u_conv1_layer.u_conv1_buf.x_count};
                "CTL_0027": fi_after_word={{315{1'b0}}, dut.u_conv1_layer.u_conv1_buf.y_count};
                "CTL_0028": fi_after_word={{318{1'b0}}, dut.u_conv2_layer.u_conv2_buf.state};
                "CTL_0029": fi_after_word={{312{1'b0}}, dut.u_conv2_layer.u_conv2_buf.capture_word_count};
                "CTL_0030": fi_after_word={{316{1'b0}}, dut.u_conv2_layer.u_conv2_buf.out_y};
                "CTL_0031": fi_after_word={{316{1'b0}}, dut.u_conv2_layer.u_conv2_buf.out_x};
                "BUF_0017": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.line_buf1[0]};
                "BUF_0018": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.line_buf1[14]};
                "BUF_0019": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.line_buf1[27]};
                "BUF_0020": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.line_buf2[0]};
                "BUF_0021": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.line_buf2[14]};
                "BUF_0022": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.line_buf2[27]};
                "BUF_0023": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.top_d1};
                "BUF_0024": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.top_d2};
                "BUF_0025": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.mid_d1};
                "BUF_0026": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.mid_d2};
                "BUF_0027": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.bot_d1};
                "BUF_0028": fi_after_word={{312{1'b0}}, dut.u_conv1_layer.u_conv1_buf.bot_d2};
                "BUF_0029": fi_after_word={{248{1'b0}}, dut.u_conv1_layer.u_conv1_buf.o_window};
                "BUF_0030": fi_after_word={{312{1'b0}}, dut.u_conv2_layer.u_conv2_buf.feature_mem[0]};
                "BUF_0031": fi_after_word={{312{1'b0}}, dut.u_conv2_layer.u_conv2_buf.feature_mem[337]};
                "BUF_0032": fi_after_word={{312{1'b0}}, dut.u_conv2_layer.u_conv2_buf.feature_mem[675]};
                "BUF_0033": fi_after_word={{32{1'b0}}, dut.u_conv2_layer.u_conv2_buf.o_window};
                default: $fatal(1,"FI_INFRA_ERROR: after-read target mismatch");
            endcase

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



    // ========================================================================
    // OPTIONAL FAULT-FREE TIMING PROFILE. Simulation-only diagnostics.
    // PRE-NBA qualification, post-NBA cycle labels. No RTL modification.
    // A profile provides evidence of writes/reads, NOT of fault exposure.
    // ========================================================================
    reg pilot_profile_only = 1'b0;
    reg pr_mem1_w0, pr_mem1_w84, pr_mem1_w168;
    reg pr_mem1_r0, pr_mem1_r84, pr_mem1_r168;
    reg pr_mem2_w0, pr_mem2_w12, pr_mem2_w24;
    reg pr_mem2_r0, pr_mem2_r12, pr_mem2_r24;
    reg pr_buf0_cap, pr_buf84_cap, pr_buf168_cap;
    reg pr_reorder337_write, pr_conv1_line0_write;
    reg pr_fc_valid;

    always @(posedge i_clk) begin
        if (i_rst_n && monitor_enable && !fault_run && pilot_profile_only) begin
            pr_mem1_w0 = dut.u_feature_mem1.i_wr_valid &&
                (dut.u_feature_mem1.i_start || dut.u_feature_mem1.wr_ptr == 8'd0);
            pr_mem1_w84 = dut.u_feature_mem1.i_wr_valid &&
                !dut.u_feature_mem1.i_start && dut.u_feature_mem1.wr_ptr == 8'd84;
            pr_mem1_w168 = dut.u_feature_mem1.i_wr_valid &&
                !dut.u_feature_mem1.i_start && dut.u_feature_mem1.wr_ptr == 8'd168;
            pr_mem1_r0 = dut.u_feature_mem1.rd_active &&
                dut.u_feature_mem1.rd_ptr == 8'd0 && !dut.u_feature_mem1.i_start_rd;
            pr_mem1_r84 = dut.u_feature_mem1.rd_active &&
                dut.u_feature_mem1.rd_ptr == 8'd84 && !dut.u_feature_mem1.i_start_rd;
            pr_mem1_r168 = dut.u_feature_mem1.rd_active &&
                dut.u_feature_mem1.rd_ptr == 8'd168 && !dut.u_feature_mem1.i_start_rd;
            pr_mem2_w0 = dut.u_feature_mem2.i_wr_valid &&
                (dut.u_feature_mem2.i_start || dut.u_feature_mem2.wr_addr == 5'd0);
            pr_mem2_w12 = dut.u_feature_mem2.i_wr_valid &&
                !dut.u_feature_mem2.i_start && dut.u_feature_mem2.wr_addr == 5'd12;
            pr_mem2_w24 = dut.u_feature_mem2.i_wr_valid &&
                !dut.u_feature_mem2.i_start && dut.u_feature_mem2.wr_addr == 5'd24;
            pr_mem2_r0 = dut.u_feature_mem2.rd_active &&
                dut.u_feature_mem2.rd_addr == 5'd0 && !dut.u_feature_mem2.i_start_rd;
            pr_mem2_r12 = dut.u_feature_mem2.rd_active &&
                dut.u_feature_mem2.rd_addr == 5'd12 && !dut.u_feature_mem2.i_start_rd;
            pr_mem2_r24 = dut.u_feature_mem2.rd_active &&
                dut.u_feature_mem2.rd_addr == 5'd24 && !dut.u_feature_mem2.i_start_rd;
            pr_buf0_cap = dut.u_conv2_layer.u_conv2_buf.i_valid &&
                ((dut.u_conv2_layer.u_conv2_buf.state == 2'd0 &&
                  dut.u_conv2_layer.u_conv2_buf.i_start) ||
                 (dut.u_conv2_layer.u_conv2_buf.state == 2'd1 &&
                  dut.u_conv2_layer.u_conv2_buf.capture_word_count == 8'd0));
            pr_buf84_cap = dut.u_conv2_layer.u_conv2_buf.state == 2'd1 &&
                dut.u_conv2_layer.u_conv2_buf.i_valid &&
                dut.u_conv2_layer.u_conv2_buf.capture_word_count == 8'd84;
            pr_buf168_cap = dut.u_conv2_layer.u_conv2_buf.state == 2'd1 &&
                dut.u_conv2_layer.u_conv2_buf.i_valid &&
                dut.u_conv2_layer.u_conv2_buf.capture_word_count == 8'd168;
            pr_reorder337_write = dut.u_conv1_layer.u_pool1_reorder.state == 2'd1 &&
                dut.u_conv1_layer.u_pool1_reorder.i_valid &&
                dut.u_conv1_layer.u_pool1_reorder.capture_count == 8'd168;
            pr_conv1_line0_write = dut.u_conv1_layer.u_conv1_buf.i_valid &&
                dut.u_conv1_layer.u_conv1_buf.x_count == 5'd0;
            pr_fc_valid = dut.fc_valid;
            #2;
            if (pr_mem1_w0) $display("FI_PROFILE_EVENT,WRITE,MEM_0001,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem1_w84) $display("FI_PROFILE_EVENT,WRITE,MEM_0002,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem1_w168) $display("FI_PROFILE_EVENT,WRITE,MEM_0003,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem1_r0) $display("FI_PROFILE_EVENT,READ,MEM_0001,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem1_r84) $display("FI_PROFILE_EVENT,READ,MEM_0002,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem1_r168) $display("FI_PROFILE_EVENT,READ,MEM_0003,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem2_w0) $display("FI_PROFILE_EVENT,WRITE,MEM_0004,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem2_w12) $display("FI_PROFILE_EVENT,WRITE,MEM_0005,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem2_w24) $display("FI_PROFILE_EVENT,WRITE,MEM_0006,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem2_r0) $display("FI_PROFILE_EVENT,READ,MEM_0004,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem2_r12) $display("FI_PROFILE_EVENT,READ,MEM_0005,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem2_r24) $display("FI_PROFILE_EVENT,READ,MEM_0006,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_buf0_cap) $display("FI_PROFILE_EVENT,WRITE,BUF_0030,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_buf84_cap) $display("FI_PROFILE_EVENT,WRITE,BUF_0031,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_buf168_cap) $display("FI_PROFILE_EVENT,WRITE,BUF_0032,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_reorder337_write) $display("FI_PROFILE_EVENT,WRITE,BUF_0007,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_conv1_line0_write) $display("FI_PROFILE_EVENT,WRITE,BUF_0017,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_fc_valid) $display("FI_PROFILE_EVENT,FC_VALID,DAT_0010,%0d,%0d",global_cycle,global_cycle-start_cycle);
        end
    end

    // ========================================================================
    // PILOT400 PROFILE V2: target-specific RTL state activity and handoff.
    // Only active during the fault-free control run (+PILOT_PROFILE_ONLY).
    // All predicates below are sampled PRE-NBA at the consuming clock edge;
    // global cycle is printed after #2, consistently with the legacy profile.
    //
    // ACTIVE: the indicated CTL register is in an operative phase and read
    //         or advanced at the sampled edge (NOT proof of harmfulness).
    // PRODUCE: a registered DAT value is written by NBA at this edge.
    // CONSUME: a downstream block samples the OLD registered DAT value at
    //          this edge (NOT a value-aware corrupted-data assertion).
    //
    // For BUF/MEM, CONSUME records baseline downstream access to the selected
    // slot. They do not assert that an injected bit reaches the consumer.
    // ========================================================================
    reg pr_ctl0001, pr_ctl0012, pr_ctl0019, pr_ctl0026, pr_ctl0028;
    reg pr_dat0002_prod, pr_dat0002_use;
    reg pr_dat0004_prod, pr_dat0004_use;
    reg pr_fc_acc_prod, pr_fc_acc_use;
    reg pr_dat0010_prod, pr_dat0010_use;
    reg pr_buf0007_use, pr_buf0017_use;
    reg pr_mem1_use0, pr_mem1_use84, pr_mem1_use168;
    reg pr_mem2_use0, pr_mem2_use12, pr_mem2_use24;
    reg pr_buf0030_use, pr_buf0031_use, pr_buf0032_use;

    always @(posedge i_clk) begin
        if (i_rst_n && monitor_enable && !fault_run && pilot_profile_only) begin
            // Verilog state names/widths are taken from the uploaded P0 RTL.
            pr_ctl0001 = (dut.u_top_fsm.state >= 3'd1) &&
                         (dut.u_top_fsm.state <= 3'd4);
            pr_ctl0012 = dut.u_feature_mem1.i_wr_valid &&
                         !dut.u_feature_mem1.i_start &&
                         (dut.u_feature_mem1.wr_ptr < 8'd169);
            pr_ctl0019 = dut.u_fully_connected.r_busy &&
                         dut.u_fully_connected.i_valid &&
                         (dut.u_fully_connected.cycle_count <= 5'd24);
            pr_ctl0026 = dut.u_conv1_layer.u_conv1_buf.i_valid &&
                         !dut.u_conv1_layer.u_conv1_buf.i_start;
            pr_ctl0028 = ((dut.u_conv2_layer.u_conv2_buf.state == 2'd1) &&
                          dut.u_conv2_layer.u_conv2_buf.i_valid) ||
                         (dut.u_conv2_layer.u_conv2_buf.state == 2'd3);

            pr_dat0002_prod = dut.u_conv1_layer.u_conv1_quant_relu.i_valid;
            pr_dat0002_use  = dut.u_conv1_layer.u_pool1_buf.i_valid;
            pr_dat0004_prod = dut.u_conv2_layer.u_conv2_calc.i_valid;
            pr_dat0004_use  = dut.u_conv2_layer.u_conv2_quant_relu.i_valid;
            // FC initializes accumulator on i_start and then updates/uses it
            // on each accepted input word; cycle24 also reads it for logits.
            pr_fc_acc_prod = (dut.u_fully_connected.i_start &&
                              !dut.u_fully_connected.r_busy) ||
                             (dut.u_fully_connected.r_busy &&
                              dut.u_fully_connected.i_valid);
            pr_fc_acc_use  = dut.u_fully_connected.r_busy &&
                             dut.u_fully_connected.i_valid;
            pr_dat0010_prod = dut.u_fully_connected.r_busy &&
                              dut.u_fully_connected.i_valid &&
                              (dut.u_fully_connected.cycle_count == 5'd24);
            pr_dat0010_use  = dut.u_argmax.i_valid;

            // Pool1 canonical byte337 is emitted in reordered word84,
            // accepted by Mem1 at the following posedge.
            pr_buf0007_use = dut.u_feature_mem1.i_wr_valid &&
                             !dut.u_feature_mem1.i_start &&
                             (dut.u_feature_mem1.wr_ptr == 8'd84);
            // line_buf1[0] is sampled by the shift register at the next
            // row's x=0 edge, before the nonblocking write overwrites it.
            pr_buf0017_use = dut.u_conv1_layer.u_conv1_buf.i_valid &&
                             !dut.u_conv1_layer.u_conv1_buf.i_start &&
                             (dut.u_conv1_layer.u_conv1_buf.x_count == 5'd0) &&
                             (dut.u_conv1_layer.u_conv1_buf.y_count >= 5'd2);

            // Mem read outputs are registered; the downstream blocks consume
            // the previous o_rd_valid/o_rd_data on their following edge.
            pr_mem1_use0 = 0;
            pr_mem1_use84 = 0;
            pr_mem1_use168 = 0;
            if (dut.u_conv2_layer.u_conv2_buf.i_valid) begin
                if (pr_mem1_use_index == 0)   pr_mem1_use0 = 1;
                if (pr_mem1_use_index == 84)  pr_mem1_use84 = 1;
                if (pr_mem1_use_index == 168) pr_mem1_use168 = 1;
                pr_mem1_use_index = pr_mem1_use_index + 1;
            end
            pr_mem2_use0 = 0;
            pr_mem2_use12 = 0;
            pr_mem2_use24 = 0;
            if (dut.u_fully_connected.i_valid) begin
                if (pr_mem2_use_index == 0)  pr_mem2_use0 = 1;
                if (pr_mem2_use_index == 12) pr_mem2_use12 = 1;
                if (pr_mem2_use_index == 24) pr_mem2_use24 = 1;
                pr_mem2_use_index = pr_mem2_use_index + 1;
            end
            pr_buf0030_use = 0;
            pr_buf0031_use = 0;
            pr_buf0032_use = 0;
            if (dut.u_conv2_layer.u_conv2_calc.i_valid) begin
                if (pr_conv2_use_index == 0) pr_buf0030_use = 1;
                if (pr_conv2_use_index == 120) begin
                    pr_buf0031_use = 1;
                    pr_buf0032_use = 1;
                end
                pr_conv2_use_index = pr_conv2_use_index + 1;
            end

            #2;
            if (pr_ctl0001) $display("FI_PROFILE_EVENT,ACTIVE,CTL_0001,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_ctl0012) $display("FI_PROFILE_EVENT,ACTIVE,CTL_0012,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_ctl0019) $display("FI_PROFILE_EVENT,ACTIVE,CTL_0019,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_ctl0026) $display("FI_PROFILE_EVENT,ACTIVE,CTL_0026,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_ctl0028) $display("FI_PROFILE_EVENT,ACTIVE,CTL_0028,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_dat0002_prod) $display("FI_PROFILE_EVENT,PRODUCE,DAT_0002,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_dat0002_use)  $display("FI_PROFILE_EVENT,CONSUME,DAT_0002,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_dat0004_prod) $display("FI_PROFILE_EVENT,PRODUCE,DAT_0004,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_dat0004_use)  $display("FI_PROFILE_EVENT,CONSUME,DAT_0004,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_fc_acc_prod) begin
                $display("FI_PROFILE_EVENT,PRODUCE,DAT_0007,%0d,%0d",global_cycle,global_cycle-start_cycle);
                $display("FI_PROFILE_EVENT,PRODUCE,DAT_0008,%0d,%0d",global_cycle,global_cycle-start_cycle);
            end
            if (pr_fc_acc_use) begin
                $display("FI_PROFILE_EVENT,CONSUME,DAT_0007,%0d,%0d",global_cycle,global_cycle-start_cycle);
                $display("FI_PROFILE_EVENT,CONSUME,DAT_0008,%0d,%0d",global_cycle,global_cycle-start_cycle);
            end
            if (pr_dat0010_prod) $display("FI_PROFILE_EVENT,PRODUCE,DAT_0010,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_dat0010_use)  $display("FI_PROFILE_EVENT,CONSUME,DAT_0010,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_buf0007_use) $display("FI_PROFILE_EVENT,CONSUME,BUF_0007,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_buf0017_use) $display("FI_PROFILE_EVENT,CONSUME,BUF_0017,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem1_use0) $display("FI_PROFILE_EVENT,CONSUME,MEM_0001,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem1_use84) $display("FI_PROFILE_EVENT,CONSUME,MEM_0002,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem1_use168) $display("FI_PROFILE_EVENT,CONSUME,MEM_0003,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem2_use0) $display("FI_PROFILE_EVENT,CONSUME,MEM_0004,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem2_use12) $display("FI_PROFILE_EVENT,CONSUME,MEM_0005,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_mem2_use24) $display("FI_PROFILE_EVENT,CONSUME,MEM_0006,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_buf0030_use) $display("FI_PROFILE_EVENT,CONSUME,BUF_0030,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_buf0031_use) $display("FI_PROFILE_EVENT,CONSUME,BUF_0031,%0d,%0d",global_cycle,global_cycle-start_cycle);
            if (pr_buf0032_use) $display("FI_PROFILE_EVENT,CONSUME,BUF_0032,%0d,%0d",global_cycle,global_cycle-start_cycle);
        end
    end

    // ========================================================================
    // OPTIONAL VALUE-LEVEL MONITORS for MEM_0002 / BUF_0031 bit0 only
    // Derived from verified generic FI V2 TB. No DUT RTL changes.
    // Sample pre-NBA write/read conditions at posedge, check registered values
    // after #2. Check the Conv2 calc inputs BEFORE the consuming posedge NBA.
    // ========================================================================
    integer tl_write_cycle, tl_read_cycle, tl_capture_cycle, tl_inject_cycle;
    integer tl_emit119_cycle, tl_consume119_cycle;
    integer tl_emit120_cycle, tl_consume120_cycle;
    reg [31:0] tl_mem_word_original;
    reg [7:0]  tl_buf_byte_original;
    reg [7:0]  tl_expected_corrupt_byte;
    reg tl_pending119, tl_pending120;

    // These event snapshots are sampled before sequential DUT NBAs.
    reg tl_ev_mem_write, tl_ev_mem_read, tl_ev_buf_capture;
    reg tl_ev_emit119, tl_ev_emit120;
    reg tl_ev_cons119, tl_ev_cons120;
    reg [31:0] tl_write_input, tl_capture_input;

    always @(negedge i_rst_n) begin
        tl_write_cycle = -1;
        tl_read_cycle = -1;
        tl_capture_cycle = -1;
        tl_inject_cycle = -1;
        tl_emit119_cycle = -1;
        tl_consume119_cycle = -1;
        tl_emit120_cycle = -1;
        tl_consume120_cycle = -1;
        tl_mem_word_original = 32'd0;
        tl_buf_byte_original = 8'd0;
        tl_expected_corrupt_byte = 8'd0;
        tl_pending119 = 1'b0;
        tl_pending120 = 1'b0;
    end

    always @(posedge i_clk) begin
        if (i_rst_n && monitor_enable && fault_run &&
            ((target_id == "MEM_0002") || (target_id == "BUF_0031"))) begin

            tl_ev_mem_write = (target_id == "MEM_0002") &&
                !dut.u_feature_mem1.i_start &&
                dut.u_feature_mem1.i_wr_valid &&
                (dut.u_feature_mem1.wr_ptr == 8'd84);
            tl_ev_mem_read = (target_id == "MEM_0002") &&
                !dut.u_feature_mem1.i_start_rd &&
                dut.u_feature_mem1.rd_active &&
                (dut.u_feature_mem1.rd_ptr == 8'd84);
            tl_ev_buf_capture =
                (dut.u_conv2_layer.u_conv2_buf.state == 2'd1) &&
                dut.u_conv2_layer.u_conv2_buf.i_valid &&
                (dut.u_conv2_layer.u_conv2_buf.capture_word_count == 8'd84);

            tl_ev_emit119 = (target_id == "MEM_0002") &&
                (dut.u_conv2_layer.u_conv2_buf.state == 2'd3) &&
                (dut.u_conv2_layer.u_conv2_buf.out_y == 4'd10) &&
                (dut.u_conv2_layer.u_conv2_buf.out_x == 4'd9);
            tl_ev_emit120 =
                (dut.u_conv2_layer.u_conv2_buf.state == 2'd3) &&
                (dut.u_conv2_layer.u_conv2_buf.out_y == 4'd10) &&
                (dut.u_conv2_layer.u_conv2_buf.out_x == 4'd10);

            tl_ev_cons119 = tl_pending119;
            tl_ev_cons120 = tl_pending120;
            tl_write_input = dut.u_feature_mem1.i_wr_data;
            tl_capture_input = dut.u_conv2_layer.u_conv2_buf.i_data;

            // Conv2 calculator consumes the OLD registered buffer output at
            // this rising edge, before the buffer's next NBA update.
            if (tl_ev_cons119) begin
                if (dut.u_conv2_layer.u_conv2_calc.i_valid !== 1'b1 ||
                    dut.u_conv2_layer.u_conv2_calc.i_window[143:136] !==
                        tl_expected_corrupt_byte)
                    $fatal(1, "FI_TIMELINE_FAIL: MEM window119 not consumed with corrupted ch1 P8");
            end
            if (tl_ev_cons120) begin
                if (dut.u_conv2_layer.u_conv2_calc.i_valid !== 1'b1)
                    $fatal(1, "FI_TIMELINE_FAIL: final window not consumed");
                if ((target_id == "MEM_0002" &&
                     dut.u_conv2_layer.u_conv2_calc.i_window[135:128] !==
                         tl_expected_corrupt_byte) ||
                    (target_id == "BUF_0031" &&
                     dut.u_conv2_layer.u_conv2_calc.i_window[143:136] !==
                         tl_expected_corrupt_byte))
                    $fatal(1, "FI_TIMELINE_FAIL: window120 consumer byte mismatch");
            end

            // All sampled posedge events above are PRE-NBA; DUT outputs and
            // global_cycle are stable at #2 (XSim testbench convention).
            #2;

            if (tl_ev_mem_write) begin
                if (tl_write_cycle >= 0)
                    $fatal(1, "FI_TIMELINE_FAIL: repeated MEM word84 write");
                tl_write_cycle = global_cycle;
                tl_mem_word_original = tl_write_input;
                if (dut.u_feature_mem1.mem[84] !== tl_write_input)
                    $fatal(1, "FI_TIMELINE_FAIL: MEM word84 write not stored");
                $display("FI_TIMELINE_MEM_WRITE cycle=%0d value=%08h", global_cycle, tl_write_input);
            end

            if (fi_injection_ok && (tl_inject_cycle < 0)) begin
                tl_inject_cycle = global_cycle;
                if (tl_inject_cycle != fi_requested_cycle)
                    $fatal(1, "FI_TIMELINE_FAIL: injection cycle mismatch");
                if (target_id == "MEM_0002") begin
                    if (tl_write_cycle < 0 ||
                        dut.u_feature_mem1.mem[84] !== (tl_mem_word_original ^ 32'h00000001))
                        $fatal(1, "FI_TIMELINE_FAIL: MEM word84 flip not retained at injection");
                    tl_expected_corrupt_byte = tl_mem_word_original[7:0] ^ 8'h01;
                end else begin
                    if (tl_capture_cycle < 0 ||
                        dut.u_conv2_layer.u_conv2_buf.feature_mem[337] !==
                            (tl_buf_byte_original ^ 8'h01))
                        $fatal(1, "FI_TIMELINE_FAIL: BUF byte337 flip not retained at injection");
                end
                $display("FI_TIMELINE_INJECT cycle=%0d target=%0s", tl_inject_cycle, target_id);
            end

            if (tl_ev_mem_read) begin
                if (tl_read_cycle >= 0 || tl_inject_cycle < 0 ||
                    dut.u_feature_mem1.mem[84] !== (tl_mem_word_original ^ 32'h00000001))
                    $fatal(1, "FI_TIMELINE_FAIL: MEM read order/retention mismatch");
                if (dut.u_feature_mem1.o_rd_valid !== 1'b1 ||
                    dut.u_feature_mem1.o_rd_data !== (tl_mem_word_original ^ 32'h00000001))
                    $fatal(1, "FI_TIMELINE_FAIL: MEM registered read did not carry flipped bit");
                tl_read_cycle = global_cycle;
                $display("FI_TIMELINE_MEM_READ cycle=%0d word=%08h", global_cycle,
                    dut.u_feature_mem1.o_rd_data);
            end

            if (tl_ev_buf_capture) begin
                if (tl_capture_cycle >= 0)
                    $fatal(1, "FI_TIMELINE_FAIL: repeated Conv2 buffer word84 capture");
                tl_capture_cycle = global_cycle;
                if (target_id == "MEM_0002") begin
                    if (tl_read_cycle < 0 ||
                        tl_capture_input !== (tl_mem_word_original ^ 32'h00000001) ||
                        dut.u_conv2_layer.u_conv2_buf.feature_mem[336] !==
                            tl_expected_corrupt_byte)
                        $fatal(1, "FI_TIMELINE_FAIL: MEM corruption not captured by Conv2 BUF");
                end else begin
                    tl_buf_byte_original = tl_capture_input[15:8];
                    tl_expected_corrupt_byte = tl_buf_byte_original ^ 8'h01;
                    if (dut.u_conv2_layer.u_conv2_buf.feature_mem[337] !== tl_buf_byte_original)
                        $fatal(1, "FI_TIMELINE_FAIL: BUF byte337 capture mismatch");
                end
                $display("FI_TIMELINE_BUF_CAPTURE cycle=%0d data=%08h", global_cycle,
                    tl_capture_input);
            end

            if (tl_ev_emit119) begin
                if (tl_emit119_cycle >= 0 || tl_capture_cycle < 0 ||
                    dut.u_conv2_layer.u_conv2_buf.o_valid !== 1'b1 ||
                    dut.u_conv2_layer.u_conv2_buf.o_window[143:136] !==
                        tl_expected_corrupt_byte)
                    $fatal(1, "FI_TIMELINE_FAIL: MEM corruption not emitted in window119");
                tl_emit119_cycle = global_cycle;
                tl_pending119 = 1'b1;
                $display("FI_TIMELINE_WINDOW119_EMIT cycle=%0d byte=%02h",
                    global_cycle, tl_expected_corrupt_byte);
            end

            if (tl_ev_emit120) begin
                if (tl_emit120_cycle >= 0 || tl_inject_cycle < 0 ||
                    dut.u_conv2_layer.u_conv2_buf.o_valid !== 1'b1)
                    $fatal(1, "FI_TIMELINE_FAIL: window120 emit invalid");
                if (target_id == "MEM_0002") begin
                    if (dut.u_conv2_layer.u_conv2_buf.feature_mem[336] !==
                            tl_expected_corrupt_byte ||
                        dut.u_conv2_layer.u_conv2_buf.o_window[135:128] !==
                            tl_expected_corrupt_byte)
                        $fatal(1, "FI_TIMELINE_FAIL: MEM corrupted P7 missing in window120");
                end else begin
                    if (dut.u_conv2_layer.u_conv2_buf.feature_mem[337] !==
                            tl_expected_corrupt_byte ||
                        dut.u_conv2_layer.u_conv2_buf.o_window[143:136] !==
                            tl_expected_corrupt_byte)
                        $fatal(1, "FI_TIMELINE_FAIL: BUF corrupted P8 missing in window120");
                end
                tl_emit120_cycle = global_cycle;
                tl_pending120 = 1'b1;
                $display("FI_TIMELINE_WINDOW120_EMIT cycle=%0d byte=%02h",
                    global_cycle, tl_expected_corrupt_byte);
            end

            if (tl_ev_cons119) begin
                tl_consume119_cycle = global_cycle;
                tl_pending119 = 1'b0;
                $display("FI_TIMELINE_WINDOW119_CONSUME cycle=%0d", global_cycle);
            end
            if (tl_ev_cons120) begin
                tl_consume120_cycle = global_cycle;
                tl_pending120 = 1'b0;
                $display("FI_TIMELINE_WINDOW120_CONSUME cycle=%0d", global_cycle);
            end
        end
    end

    task timeline_check;
        begin
            if (target_id == "MEM_0002") begin
                if (!(tl_write_cycle >= 0 && tl_write_cycle < tl_inject_cycle &&
                      tl_inject_cycle < tl_read_cycle &&
                      tl_read_cycle < tl_capture_cycle &&
                      tl_capture_cycle < tl_emit119_cycle &&
                      tl_emit119_cycle < tl_consume119_cycle &&
                      tl_consume119_cycle == tl_emit120_cycle &&
                      tl_emit120_cycle < tl_consume120_cycle))
                    $fatal(1, "FI_TIMELINE_FAIL: MEM timeline incomplete/nonmonotonic");
                $display("FI_TIMELINE_MEM_PASS write=%0d inject=%0d read=%0d capture=%0d emit119=%0d consume119=%0d emit120=%0d consume120=%0d",
                    tl_write_cycle, tl_inject_cycle, tl_read_cycle,
                    tl_capture_cycle, tl_emit119_cycle, tl_consume119_cycle,
                    tl_emit120_cycle, tl_consume120_cycle);
            end else if (target_id == "BUF_0031") begin
                if (!(tl_capture_cycle >= 0 && tl_capture_cycle < tl_inject_cycle &&
                      tl_inject_cycle < tl_emit120_cycle &&
                      tl_emit120_cycle < tl_consume120_cycle))
                    $fatal(1, "FI_TIMELINE_FAIL: BUF timeline incomplete/nonmonotonic");
                $display("FI_TIMELINE_BUF_PASS capture=%0d inject=%0d emit120=%0d consume120=%0d",
                    tl_capture_cycle, tl_inject_cycle,
                    tl_emit120_cycle, tl_consume120_cycle);
            end else begin
                $fatal(1, "FI_TIMELINE_FAIL: unsupported target %0s", target_id);
            end
        end
    endtask


    // ========================================================================
    // V3b DAT_0004 isolated value-aware consumer proof (simulation-only).
    // Producer: conv2_calc.o_acc lane0 (registered signed INT32 Q8.8).
    // Consumer: conv2_quant_relu.i_acc lane0 with i_valid HIGH at the
    //           NEXT posedge, sampled PRE-NBA, not an output-only inference.
    // Also verify that the quant/relu registered output captures the SAME
    // pre-NBA combinational relu_bus lane0 after NBA. This is not evidence
    // of altered quantized byte vs fault-free data.
    // ========================================================================
    integer vd4_produce_cycle, vd4_source_cycle, vd4_inject_cycle, vd4_consume_cycle, vd4_capture_cycle;
    integer vd4_overwritten_before, vd4_concurrent_producer;
    reg [31:0] vd4_last_produced, vd4_original, vd4_corrupted;
    reg [7:0] vd4_relu_byte;
    reg vd4_prod_edge, vd4_consume_edge;
    reg vd4_calc_valid_pre, vd4_quant_valid_pre;
    reg [31:0] vd4_calc_word_pre, vd4_quant_word_pre;
    reg [255:0] vd4_quant_full_pre;
    reg [7:0] vd4_relu_pre;

    always @(negedge i_rst_n) begin
        vd4_produce_cycle = -1;
        vd4_source_cycle = -1;
        vd4_inject_cycle = -1;
        vd4_consume_cycle = -1;
        vd4_capture_cycle = -1;
        vd4_overwritten_before = 0;
        vd4_concurrent_producer = 0;
        vd4_last_produced = 0;
        vd4_original = 0;
        vd4_corrupted = 0;
        vd4_relu_byte = 0;
    end

    always @(posedge i_clk) begin
        if (i_rst_n && monitor_enable && fault_run && target_id == "DAT_0004") begin
            // Both flags and data are sampled BEFORE the consumer's NBA.
            // The injection took place at the preceding negedge + 1ns.
            vd4_prod_edge = dut.u_conv2_layer.u_conv2_calc.i_valid;
            vd4_consume_edge = fi_injection_ok && vd4_inject_cycle < 0;
            vd4_calc_valid_pre = dut.u_conv2_layer.u_conv2_calc.o_valid;
            vd4_quant_valid_pre = dut.u_conv2_layer.u_conv2_quant_relu.i_valid;
            vd4_calc_word_pre = dut.u_conv2_layer.u_conv2_calc.o_acc[31:0];
            vd4_quant_word_pre = dut.u_conv2_layer.u_conv2_quant_relu.i_acc[31:0];
            vd4_quant_full_pre = dut.u_conv2_layer.u_conv2_quant_relu.i_acc;
            vd4_relu_pre = dut.u_conv2_layer.u_conv2_quant_relu.relu_bus[7:0];
            if (vd4_consume_edge) begin
                if (fi_bit != 0 || vd4_calc_valid_pre !== 1'b1 ||
                    vd4_quant_valid_pre !== 1'b1 ||
                    vd4_calc_word_pre !== fi_after_word[31:0] ||
                    vd4_quant_word_pre !== fi_after_word[31:0] ||
                    vd4_quant_full_pre !== fi_after_word[255:0] ||
                    (fi_before_word[255:0] ^ fi_after_word[255:0]) !== 256'h1 ||
                    (^vd4_relu_pre === 1'bx))
                    $fatal(1, "FI_V3B_DAT0004_QUANT_INPUT_NOT_PROVED");
            end
            #2;
            if (vd4_consume_edge) begin
                if (vd4_produce_cycle != global_cycle-1 ||
                    vd4_last_produced !== fi_before_word[31:0] ||
                    global_cycle != fi_requested_cycle ||
                    vd4_consume_cycle >= 0)
                    $fatal(1, "FI_V3B_DAT0004_PRODUCER_LIFETIME_INVALID");
                if (dut.u_conv2_layer.u_conv2_quant_relu.o_valid !== 1'b1 ||
                    dut.u_conv2_layer.u_conv2_quant_relu.o_data[7:0] !== vd4_relu_pre)
                    $fatal(1, "FI_V3B_DAT0004_QUANT_CAPTURE_NOT_PROVED");
                vd4_source_cycle = vd4_produce_cycle;
                vd4_inject_cycle = global_cycle;
                vd4_consume_cycle = global_cycle;
                vd4_capture_cycle = global_cycle;
                vd4_original = fi_before_word[31:0];
                vd4_corrupted = fi_after_word[31:0];
                vd4_relu_byte = vd4_relu_pre;
                // A producer active on the SAME edge may overwrite o_acc
                // via NBA AFTER the consumer has already taken the old value.
                vd4_concurrent_producer = (vd4_prod_edge === 1'b1) ? 1 : 0;
                $display("FI_V3B_DAT0004_PRODUCE scenario=%0s target=%0s cycle=%0d lane0=%08h",
                         scenario_id,target_id,vd4_produce_cycle,vd4_last_produced);
                $display("FI_V3B_DAT0004_INJECT scenario=%0s target=%0s cycle=%0d bit=%0d before=%08h after=%08h",
                         scenario_id,target_id,vd4_inject_cycle,fi_bit,vd4_original,vd4_corrupted);
                $display("FI_V3B_DAT0004_QUANT_ACCEPT scenario=%0s target=%0s cycle=%0d raw=%08h relu=%02h",
                         scenario_id,target_id,vd4_consume_cycle,vd4_quant_word_pre,vd4_relu_byte);
                $display("FI_V3B_DAT0004_QUANT_CAPTURE scenario=%0s target=%0s cycle=%0d valid=%0d relu=%02h concurrent_producer=%0d",
                         scenario_id,target_id,vd4_capture_cycle,
                         dut.u_conv2_layer.u_conv2_quant_relu.o_valid,
                         dut.u_conv2_layer.u_conv2_quant_relu.o_data[7:0],
                         vd4_concurrent_producer);
            end
            // Producer's NEW registered value must be sampled POST-NBA.
            // This allows an overlapping valid to overwrite o_acc at the
            // consumer edge without falsely declaring "overwritten before".
            if (vd4_prod_edge) begin
                vd4_produce_cycle = global_cycle;
                vd4_last_produced = dut.u_conv2_layer.u_conv2_calc.o_acc[31:0];
            end
        end
    end

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

        pilot_profile_only = $test$plusargs("PILOT_PROFILE_ONLY");
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
        if (pilot_profile_only) begin
            $display("FI_PROFILE_SCHEMA_V2: ACTIVE PRODUCE CONSUME PRE_NBA");
            $display("FI_PROFILE_PASS: baseline completed, no fault injected");
            $finish;
        end
        $display("FAULT RUN: %0s",scenario_id);
        run_case(1);

        if (fi_injection_ok != 1 || fi_events != 1)
            $fatal(1,"FI_INFRA_ERROR: expected one verified bit flip");

        // All other targets remain at injection/checkpoint observability.
        // Do NOT infer absence of consumption when no timeline was instrumented.
        if (((target_id == "MEM_0002") || (target_id == "BUF_0031")) && fi_bit == 0)
            timeline_check();


        if (target_id == "DAT_0004") begin
            if (!(vd4_source_cycle == vd4_inject_cycle-1 &&
                  vd4_inject_cycle == fi_requested_cycle &&
                  vd4_consume_cycle == vd4_inject_cycle &&
                  vd4_capture_cycle == vd4_consume_cycle &&
                  vd4_overwritten_before == 0))
                $fatal(1, "FI_V3B_DAT0004_TIMELINE_INCOMPLETE");
            $display("FI_V3B_DAT0004_SUMMARY scenario=%0s target=%0s inject=%0d consume=%0d capture=%0d overwritten_before=%0d concurrent_producer=%0d before=%08h after=%08h relu=%02h",
                     scenario_id,target_id,vd4_inject_cycle,vd4_consume_cycle,
                     vd4_capture_cycle,vd4_overwritten_before,
                     vd4_concurrent_producer,vd4_original,vd4_corrupted,vd4_relu_byte);
        end

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
