`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 10:47:30 PM
// Design Name: Nguyen Tri Tue
// Module Name: fully_connected
//
// Description:
//   Fully Connected layer for the Phase-1 CNN accelerator.
//
// Architecture:
//
//   Input:
//     200 canonical INT8 Q4.4 features.
//
//   Runtime:
//     25 accepted cycles.
//     Each accepted cycle receives 8 features.
//
//     cycle 0  -> features   0..7
//     cycle 1  -> features   8..15
//     ...
//     cycle 24 -> features 192..199
//
//   Per cycle:
//     8 features x 10 classes
//     = 80 signed INT8 multiplications.
//
// Inputs:
//
//   i_data:
//     8 signed INT8 features.
//     lane0 = [7:0]
//     ...
//     lane7 = [63:56]
//
//   i_weights:
//     10 classes x 8 signed INT8 weights.
//     weight[class][lane]
//
//     flattened:
//       ((class*8 + lane)*8) +: 8
//
//   i_biases:
//     10 signed INT8 Q4.4 biases.
//
// Arithmetic:
//
//   feature Q4.4 x weight Q4.4
//     -> Q8.8 product
//
//   accumulator:
//     signed INT32 Q8.8
//
//   bias:
//     signed INT8 Q4.4
//     -> sign extend to INT32
//     -> <<< 4
//     -> Q8.8
//
// Output:
//
//   10 signed INT32 Q8.8 logits.
//
//   logit[0] -> o_logits[31:0]
//   ...
//   logit[9] -> o_logits[319:288]
//
// IMPORTANT:
//
//   - No requantization.
//   - No saturation.
//   - No ReLU.
//   - o_rom_cycle advances ONLY on accepted i_valid.
//   - Valid gaps must not change weight group.
//   - Final cycle includes its 8 products before logits are published.
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module fully_connected (
    input  wire         i_clk,
    input  wire         i_rst_n,

    input  wire         i_start,

    input  wire         i_valid,
    input  wire [63:0]  i_data,

    input  wire [639:0] i_weights,
    input  wire [79:0]  i_biases,

    output wire         o_busy,
    output wire [4:0]   o_rom_cycle,

    output reg          o_valid,
    output reg  [319:0] o_logits,
    output reg          o_done
);

    // =========================================================================
    // Control
    // =========================================================================

    reg       r_busy;
    reg [4:0] cycle_count;

    assign o_busy      = r_busy;
    assign o_rom_cycle = cycle_count;

    // =========================================================================
    // Accumulators
    // =========================================================================

    reg signed [31:0] accumulator [0:9];

    // =========================================================================
    // 8-element signed dot product
    // =========================================================================

    function signed [31:0] dot8;

        input [63:0] features;
        input [63:0] weights;

        integer lane;

        reg signed [7:0]  feature_s;
        reg signed [7:0]  weight_s;
        reg signed [15:0] product_s;
        reg signed [31:0] sum_s;

        begin

            sum_s = 32'sd0;

            for (lane = 0; lane < 8; lane = lane + 1) begin

                feature_s =
                    $signed(
                        features[(lane*8) +: 8]
                    );

                weight_s =
                    $signed(
                        weights[(lane*8) +: 8]
                    );

                product_s =
                    feature_s * weight_s;

                sum_s =
                    sum_s
                    +
                    {{16{product_s[15]}}, product_s};

            end

            dot8 = sum_s;

        end

    endfunction

    // =========================================================================
    // Current-cycle dot products
    // =========================================================================

    wire signed [31:0] dot0;
    wire signed [31:0] dot1;
    wire signed [31:0] dot2;
    wire signed [31:0] dot3;
    wire signed [31:0] dot4;

    wire signed [31:0] dot5;
    wire signed [31:0] dot6;
    wire signed [31:0] dot7;
    wire signed [31:0] dot8_c;
    wire signed [31:0] dot9;

    assign dot0 =
        dot8(i_data, i_weights[(0*64) +: 64]);

    assign dot1 =
        dot8(i_data, i_weights[(1*64) +: 64]);

    assign dot2 =
        dot8(i_data, i_weights[(2*64) +: 64]);

    assign dot3 =
        dot8(i_data, i_weights[(3*64) +: 64]);

    assign dot4 =
        dot8(i_data, i_weights[(4*64) +: 64]);

    assign dot5 =
        dot8(i_data, i_weights[(5*64) +: 64]);

    assign dot6 =
        dot8(i_data, i_weights[(6*64) +: 64]);

    assign dot7 =
        dot8(i_data, i_weights[(7*64) +: 64]);

    assign dot8_c =
        dot8(i_data, i_weights[(8*64) +: 64]);

    assign dot9 =
        dot8(i_data, i_weights[(9*64) +: 64]);

    // =========================================================================
    // Bias conversion
    //
    // INT8 Q4.4 -> INT32 Q8.8
    //
    // sign_extend(bias) <<< 4
    // =========================================================================

    wire signed [31:0] bias0_q88;
    wire signed [31:0] bias1_q88;
    wire signed [31:0] bias2_q88;
    wire signed [31:0] bias3_q88;
    wire signed [31:0] bias4_q88;

    wire signed [31:0] bias5_q88;
    wire signed [31:0] bias6_q88;
    wire signed [31:0] bias7_q88;
    wire signed [31:0] bias8_q88;
    wire signed [31:0] bias9_q88;

    assign bias0_q88 =
        {{24{i_biases[7]}}, i_biases[7:0]} <<< 4;

    assign bias1_q88 =
        {{24{i_biases[15]}}, i_biases[15:8]} <<< 4;

    assign bias2_q88 =
        {{24{i_biases[23]}}, i_biases[23:16]} <<< 4;

    assign bias3_q88 =
        {{24{i_biases[31]}}, i_biases[31:24]} <<< 4;

    assign bias4_q88 =
        {{24{i_biases[39]}}, i_biases[39:32]} <<< 4;

    assign bias5_q88 =
        {{24{i_biases[47]}}, i_biases[47:40]} <<< 4;

    assign bias6_q88 =
        {{24{i_biases[55]}}, i_biases[55:48]} <<< 4;

    assign bias7_q88 =
        {{24{i_biases[63]}}, i_biases[63:56]} <<< 4;

    assign bias8_q88 =
        {{24{i_biases[71]}}, i_biases[71:64]} <<< 4;

    assign bias9_q88 =
        {{24{i_biases[79]}}, i_biases[79:72]} <<< 4;

    // =========================================================================
    // Sequential FC control
    // =========================================================================

    always @(posedge i_clk) begin

        if (!i_rst_n) begin

            r_busy      <= 1'b0;
            cycle_count <= 5'd0;

            o_valid  <= 1'b0;
            o_logits <= 320'd0;
            o_done   <= 1'b0;

            accumulator[0] <= 32'sd0;
            accumulator[1] <= 32'sd0;
            accumulator[2] <= 32'sd0;
            accumulator[3] <= 32'sd0;
            accumulator[4] <= 32'sd0;

            accumulator[5] <= 32'sd0;
            accumulator[6] <= 32'sd0;
            accumulator[7] <= 32'sd0;
            accumulator[8] <= 32'sd0;
            accumulator[9] <= 32'sd0;

        end
        else begin

            // Default pulse outputs.
            o_valid <= 1'b0;
            o_done  <= 1'b0;

            // =================================================================
            // Start new FC operation
            // =================================================================

            if (i_start && !r_busy) begin

                r_busy      <= 1'b1;
                cycle_count <= 5'd0;

                accumulator[0] <= bias0_q88;
                accumulator[1] <= bias1_q88;
                accumulator[2] <= bias2_q88;
                accumulator[3] <= bias3_q88;
                accumulator[4] <= bias4_q88;

                accumulator[5] <= bias5_q88;
                accumulator[6] <= bias6_q88;
                accumulator[7] <= bias7_q88;
                accumulator[8] <= bias8_q88;
                accumulator[9] <= bias9_q88;

                // -------------------------------------------------------------
                // Support i_start + i_valid on the same cycle.
                //
                // This is cycle 0.
                // -------------------------------------------------------------

                if (i_valid) begin

                    accumulator[0] <= bias0_q88 + dot0;
                    accumulator[1] <= bias1_q88 + dot1;
                    accumulator[2] <= bias2_q88 + dot2;
                    accumulator[3] <= bias3_q88 + dot3;
                    accumulator[4] <= bias4_q88 + dot4;

                    accumulator[5] <= bias5_q88 + dot5;
                    accumulator[6] <= bias6_q88 + dot6;
                    accumulator[7] <= bias7_q88 + dot7;
                    accumulator[8] <= bias8_q88 + dot8_c;
                    accumulator[9] <= bias9_q88 + dot9;

                    cycle_count <= 5'd1;

                end

            end

            // =================================================================
            // Active FC operation
            // =================================================================

            else if (r_busy && i_valid) begin

                // -------------------------------------------------------------
                // Cycles 0..23:
                // accumulate and advance ROM group.
                // -------------------------------------------------------------

                if (cycle_count < 5'd24) begin

                    accumulator[0] <= accumulator[0] + dot0;
                    accumulator[1] <= accumulator[1] + dot1;
                    accumulator[2] <= accumulator[2] + dot2;
                    accumulator[3] <= accumulator[3] + dot3;
                    accumulator[4] <= accumulator[4] + dot4;

                    accumulator[5] <= accumulator[5] + dot5;
                    accumulator[6] <= accumulator[6] + dot6;
                    accumulator[7] <= accumulator[7] + dot7;
                    accumulator[8] <= accumulator[8] + dot8_c;
                    accumulator[9] <= accumulator[9] + dot9;

                    cycle_count <= cycle_count + 5'd1;

                end

                // -------------------------------------------------------------
                // Cycle 24:
                //
                // CRITICAL:
                //
                // Output must contain:
                //
                //   old accumulator + FINAL dot product
                //
                // not merely old accumulator.
                // -------------------------------------------------------------

                else begin

                    o_logits[31:0] <=
                        accumulator[0] + dot0;

                    o_logits[63:32] <=
                        accumulator[1] + dot1;

                    o_logits[95:64] <=
                        accumulator[2] + dot2;

                    o_logits[127:96] <=
                        accumulator[3] + dot3;

                    o_logits[159:128] <=
                        accumulator[4] + dot4;


                    o_logits[191:160] <=
                        accumulator[5] + dot5;

                    o_logits[223:192] <=
                        accumulator[6] + dot6;

                    o_logits[255:224] <=
                        accumulator[7] + dot7;

                    o_logits[287:256] <=
                        accumulator[8] + dot8_c;

                    o_logits[319:288] <=
                        accumulator[9] + dot9;

                    // Keep final accumulator values coherent as well.
                    accumulator[0] <= accumulator[0] + dot0;
                    accumulator[1] <= accumulator[1] + dot1;
                    accumulator[2] <= accumulator[2] + dot2;
                    accumulator[3] <= accumulator[3] + dot3;
                    accumulator[4] <= accumulator[4] + dot4;

                    accumulator[5] <= accumulator[5] + dot5;
                    accumulator[6] <= accumulator[6] + dot6;
                    accumulator[7] <= accumulator[7] + dot7;
                    accumulator[8] <= accumulator[8] + dot8_c;
                    accumulator[9] <= accumulator[9] + dot9;

                    o_valid <= 1'b1;
                    o_done  <= 1'b1;

                    r_busy <= 1'b0;

                    // Hold at final ROM group until next start.
                    cycle_count <= 5'd24;

                end

            end

        end

    end

endmodule

`default_nettype wire