`timescale 1ns / 1ps

//////////////////////////////////////////////////////////////////////////////////
// Design Name: Nguyen Tri Tue
// Module Name: tb_conv1_quant_relu
// Description:
//   Golden verification testbench for conv1_quant_relu.
//
//   Input:
//     data/golden_conv1.hex
//     2704 signed INT32 Q8.8 accumulators
//
//   Expected:
//     data/golden_relu1.hex
//     2704 signed INT8 Q4.4 values
//
//   Golden tensor order:
//     [channel][y][x]
//
//   DUT transaction:
//     4 channels at one spatial position
//
//   Therefore:
//     golden_index = channel * 676 + window_index
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module tb_conv1_quant_relu;

    localparam NUM_WINDOWS = 676;
    localparam NUM_CHANNELS = 4;
    localparam NUM_VALUES = 2704;

    // -------------------------------------------------------------------------
    // DUT signals
    // -------------------------------------------------------------------------

    reg          i_clk;
    reg          i_rst_n;
    reg          i_valid;
    reg  [127:0] i_acc;

    wire         o_valid;
    wire [31:0]  o_data;

    // -------------------------------------------------------------------------
    // Golden memories
    // -------------------------------------------------------------------------

    reg signed [31:0] golden_conv1 [0:NUM_VALUES-1];
    reg signed [7:0]  golden_relu1 [0:NUM_VALUES-1];

    // -------------------------------------------------------------------------
    // Test variables
    // -------------------------------------------------------------------------

    integer win;
    integer ch;
    integer index;

    integer errors;
    integer checked_windows;
    integer checked_values;

    reg signed [7:0] dut_value;
    reg signed [7:0] expected_value;

    // -------------------------------------------------------------------------
    // DUT
    // -------------------------------------------------------------------------

    conv1_quant_relu dut (
        .i_clk   (i_clk),
        .i_rst_n (i_rst_n),

        .i_valid (i_valid),
        .i_acc   (i_acc),

        .o_valid (o_valid),
        .o_data  (o_data)
    );

    // -------------------------------------------------------------------------
    // Clock
    // -------------------------------------------------------------------------

    initial begin
        i_clk = 1'b0;
        forever #5 i_clk = ~i_clk;
    end

    // -------------------------------------------------------------------------
    // Pack one spatial position
    //
    // golden_conv1 is channel-major:
    //
    // channel 0 : [0 ... 675]
    // channel 1 : [676 ... 1351]
    // channel 2 : [1352 ... 2027]
    // channel 3 : [2028 ... 2703]
    //
    // DUT expects:
    //
    // i_acc[31:0]    = ch0
    // i_acc[63:32]   = ch1
    // i_acc[95:64]   = ch2
    // i_acc[127:96]  = ch3
    // -------------------------------------------------------------------------

    task pack_input;
        input integer window_index;

        integer c;
        integer golden_index;

        begin
            i_acc = 128'd0;

            for (c = 0; c < NUM_CHANNELS; c = c + 1) begin

                golden_index =
                    c * NUM_WINDOWS + window_index;

                i_acc[c*32 +: 32] =
                    golden_conv1[golden_index];

            end
        end
    endtask

    // -------------------------------------------------------------------------
    // Compare output
    // -------------------------------------------------------------------------

    task compare_output;
        input integer window_index;

        integer c;
        integer golden_index;

        begin

            if (!o_valid) begin

                $display(
                    "[FAIL] win=%0d : o_valid=0",
                    window_index
                );

                errors = errors + 1;

            end
            else begin

                for (c = 0; c < NUM_CHANNELS; c = c + 1) begin

                    golden_index =
                        c * NUM_WINDOWS + window_index;

                    dut_value =
                        $signed(o_data[c*8 +: 8]);

                    expected_value =
                        golden_relu1[golden_index];

                    if (dut_value !== expected_value) begin

                        $display(
                            "[FAIL] win=%0d ch=%0d golden_idx=%0d expected=%0d got=%0d",
                            window_index,
                            c,
                            golden_index,
                            expected_value,
                            dut_value
                        );

                        errors = errors + 1;

                    end

                    checked_values = checked_values + 1;

                end

                checked_windows = checked_windows + 1;

            end
        end
    endtask

    // -------------------------------------------------------------------------
    // Main test
    // -------------------------------------------------------------------------

    initial begin

        i_rst_n = 1'b0;
        i_valid = 1'b0;
        i_acc   = 128'd0;

        errors          = 0;
        checked_windows = 0;
        checked_values  = 0;

        // ---------------------------------------------------------------------
        // Load golden data
        // ---------------------------------------------------------------------

        $readmemh(
            "D:/Share_Data_ubuntu_window/NKKH/data/golden_conv1.hex",
            golden_conv1
        );

        $readmemh(
            "D:/Share_Data_ubuntu_window/NKKH/data/golden_relu1.hex",
            golden_relu1
        );

        // ---------------------------------------------------------------------
        // Reset
        // ---------------------------------------------------------------------

        repeat (3)
            @(posedge i_clk);

        i_rst_n = 1'b1;

        @(posedge i_clk);

        // ---------------------------------------------------------------------
        // Test all 676 spatial positions
        // ---------------------------------------------------------------------

        for (win = 0; win < NUM_WINDOWS; win = win + 1) begin

            // Set DUT input before next rising edge
            @(negedge i_clk);

            pack_input(win);

            i_valid = 1'b1;

            // DUT registers output here
            @(posedge i_clk);

            // Remove valid after edge
            @(negedge i_clk);

            i_valid = 1'b0;

            #1;

            // Compare registered output
            compare_output(win);

            // Representative debug points
            if ((win == 0)   ||
                (win == 338) ||
                (win == 675)) begin

                $display("");
                $display("win %0d:", win);

                $display(
                    "  acc  = %0d %0d %0d %0d",
                    $signed(i_acc[31:0]),
                    $signed(i_acc[63:32]),
                    $signed(i_acc[95:64]),
                    $signed(i_acc[127:96])
                );

                $display(
                    "  relu = %0d %0d %0d %0d",
                    $signed(o_data[7:0]),
                    $signed(o_data[15:8]),
                    $signed(o_data[23:16]),
                    $signed(o_data[31:24])
                );

            end
        end

        // ---------------------------------------------------------------------
        // Summary
        // ---------------------------------------------------------------------

        $display("");
        $display("==============================================");
        $display("Conv1 Quant/ReLU Golden Verification");
        $display("==============================================");

        $display(
            "Checked windows : %0d",
            checked_windows
        );

        $display(
            "Checked values  : %0d",
            checked_values
        );

        $display(
            "Errors          : %0d",
            errors
        );

        $display("");

        if ((errors == 0) &&
            (checked_windows == NUM_WINDOWS) &&
            (checked_values == NUM_VALUES)) begin

            $display(
                "========== CONV1_QUANT_RELU GOLDEN TEST: PASS =========="
            );

        end
        else begin

            $display(
                "========== CONV1_QUANT_RELU GOLDEN TEST: FAIL =========="
            );

        end

        $finish;
    end

endmodule

`default_nettype wire