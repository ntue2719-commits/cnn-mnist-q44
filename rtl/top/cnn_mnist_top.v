`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 11:03:22 PM
// Design Name: Nguyen Tri Tue
// Module Name: cnn_mnist_top
//
// Description:
//   Complete Phase-1 bit-exact MNIST CNN accelerator.
//
// Architecture:
//
//   input pixel stream
//          |
//          v
//      conv1_layer
//          |
//          v
//     feature_mem1
//          |
//          v
//      conv2_layer
//          |
//          v
//     feature_mem2
//          |
//          v
//        fc_rom
//          |
//          v
//   fully_connected
//          |
//          v
//        argmax
//          |
//          v
//      digit[3:0]
//
// Control:
//   top_fsm sequences all stages.
//
// Top-level transaction:
//   - pulse i_start
//   - stream exactly 784 accepted pixels
//   - valid gaps are legal
//   - wait for o_valid/o_done
//   - o_digit is final prediction
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module cnn_mnist_top #(
    parameter FC_WEIGHT_FILE = "fc_weight.hex",
    parameter FC_BIAS_FILE   = "fc_bias.hex"
)(
    input  wire       i_clk,
    input  wire       i_rst_n,

    input  wire       i_start,

    input  wire       i_valid,
    input  wire [7:0] i_pixel,

    output wire       o_busy,
    output wire       o_valid,
    output wire       o_done,

    output reg  [3:0] o_digit
);

    // =========================================================================
    // FSM control
    // =========================================================================

    wire start_conv1;

    wire start_rd1;
    wire start_conv2;

    wire start_rd2;
    wire start_fc;

    wire fsm_busy;
    wire fsm_valid;
    wire fsm_done;

    // =========================================================================
    // Conv1 signals
    // =========================================================================

    wire        conv1_busy;
    wire        conv1_out_valid;
    wire [31:0] conv1_out_data;
    wire        conv1_done;

    // =========================================================================
    // Mem1 signals
    // =========================================================================

    wire        mem1_rd_valid;
    wire [31:0] mem1_rd_data;

    // =========================================================================
    // Conv2 signals
    // =========================================================================

    wire        conv2_busy;
    wire        conv2_out_valid;
    wire [63:0] conv2_out_data;
    wire        conv2_done;

    // =========================================================================
    // Mem2 signals
    // =========================================================================

    wire        mem2_rd_valid;
    wire [63:0] mem2_rd_data;

    // =========================================================================
    // FC ROM
    // =========================================================================

    wire [4:0]   fc_rom_cycle;
    wire [639:0] fc_weights;
    wire [79:0]  fc_biases;

    // =========================================================================
    // FC
    // =========================================================================

    wire         fc_busy;
    wire         fc_valid;
    wire [319:0] fc_logits;
    wire         fc_done;

    // =========================================================================
    // ArgMax
    // =========================================================================

    wire       argmax_valid;
    wire [3:0] argmax_digit;

    // =========================================================================
    // Top FSM
    // =========================================================================

    top_fsm u_top_fsm (
        .i_clk          (i_clk),
        .i_rst_n        (i_rst_n),

        .i_start        (i_start),

        .i_conv1_done   (conv1_done),
        .i_conv2_done   (conv2_done),
        .i_fc_done      (fc_done),
        .i_argmax_valid (argmax_valid),

        .o_start_conv1  (start_conv1),

        .o_start_rd1    (start_rd1),
        .o_start_conv2  (start_conv2),

        .o_start_rd2    (start_rd2),
        .o_start_fc     (start_fc),

        .o_busy         (fsm_busy),
        .o_valid        (fsm_valid),
        .o_done         (fsm_done)
    );

    // =========================================================================
    // Conv1
    // =========================================================================

    conv1_layer u_conv1_layer (
        .i_clk   (i_clk),
        .i_rst_n (i_rst_n),

        .i_start (start_conv1),

        .i_valid (i_valid),
        .i_pixel (i_pixel),

        .o_busy  (conv1_busy),

        .o_valid (conv1_out_valid),
        .o_data  (conv1_out_data),

        .o_done  (conv1_done)
    );

    // =========================================================================
    // Feature Memory 1
    //
    // Writer reset/start occurs when Conv1 starts.
    // =========================================================================

    feature_mem1 u_feature_mem1 (
        .i_clk      (i_clk),
        .i_rst_n    (i_rst_n),

        .i_start    (start_conv1),

        .i_wr_valid (conv1_out_valid),
        .i_wr_data  (conv1_out_data),

        .i_start_rd (start_rd1),

        .o_rd_valid (mem1_rd_valid),
        .o_rd_data  (mem1_rd_data)
    );

    // =========================================================================
    // Conv2
    // =========================================================================

    conv2_layer u_conv2_layer (
        .i_clk   (i_clk),
        .i_rst_n (i_rst_n),

        .i_start (start_conv2),

        .i_valid (mem1_rd_valid),
        .i_data  (mem1_rd_data),

        .o_busy  (conv2_busy),

        .o_valid (conv2_out_valid),
        .o_data  (conv2_out_data),

        .o_done  (conv2_done)
    );

    // =========================================================================
    // Feature Memory 2
    //
    // Reset writer when Conv2 starts.
    // =========================================================================

    feature_mem2 u_feature_mem2 (
        .i_clk      (i_clk),
        .i_rst_n    (i_rst_n),

        .i_start    (start_conv2),

        .i_wr_valid (conv2_out_valid),
        .i_wr_data  (conv2_out_data),

        .i_start_rd (start_rd2),

        .o_rd_valid (mem2_rd_valid),
        .o_rd_data  (mem2_rd_data)
    );

    // =========================================================================
    // FC ROM
    // =========================================================================

    fc_rom #(
        .WEIGHT_FILE (FC_WEIGHT_FILE),
        .BIAS_FILE   (FC_BIAS_FILE)
    ) u_fc_rom (
        .i_cycle   (fc_rom_cycle),

        .o_weights (fc_weights),
        .o_biases  (fc_biases)
    );

    // =========================================================================
    // Fully Connected
    // =========================================================================

    fully_connected u_fully_connected (
        .i_clk       (i_clk),
        .i_rst_n     (i_rst_n),

        .i_start     (start_fc),

        .i_valid     (mem2_rd_valid),
        .i_data      (mem2_rd_data),

        .i_weights   (fc_weights),
        .i_biases    (fc_biases),

        .o_busy      (fc_busy),
        .o_rom_cycle (fc_rom_cycle),

        .o_valid     (fc_valid),
        .o_logits    (fc_logits),
        .o_done      (fc_done)
    );

    // =========================================================================
    // ArgMax
    //
    // Actual verified ArgMax interface:
    //
    //   i_valid -> o_valid
    //
    // one registered stage.
    // =========================================================================

    argmax u_argmax (
        .i_clk    (i_clk),
        .i_rst_n  (i_rst_n),

        .i_valid  (fc_valid),
        .i_logits (fc_logits),

        .o_valid  (argmax_valid),
        .o_digit  (argmax_digit)
    );

    // =========================================================================
    // Final digit register
    //
    // Capture digit before FSM reaches DONE.
    // =========================================================================

    always @(posedge i_clk) begin

        if (!i_rst_n) begin

            o_digit <= 4'd0;

        end
        else begin

            if (argmax_valid)
                o_digit <= argmax_digit;

        end

    end

    // =========================================================================
    // External control outputs
    // =========================================================================

    assign o_busy  = fsm_busy;
    assign o_valid = fsm_valid;
    assign o_done  = fsm_done;

endmodule

`default_nettype wire
