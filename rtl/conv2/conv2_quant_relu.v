`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 10:04:17 PM
// Design Name: Nguyen Tri Tue
// Module Name: conv2_quant_relu
//
// Description:
//   Conv2 post-processing block.
//
//   8 x raw signed INT32 Q8.8 accumulators
//       -> round-to-nearest-even >> 4
//       -> saturate to signed INT8 [-128,127]
//       -> ReLU
//       -> 8 x INT8 Q4.4
//
// Input packing:
//   i_acc[31:0]    = channel 0
//   i_acc[63:32]   = channel 1
//   ...
//   i_acc[255:224] = channel 7
//
// Output packing:
//   o_data[7:0]    = channel 0
//   o_data[15:8]   = channel 1
//   ...
//   o_data[63:56]  = channel 7
//
// Notes:
//   - Arithmetic primitives are the already-verified common modules.
//   - Output is registered.
//   - No arithmetic is duplicated inside this wrapper.
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module conv2_quant_relu (
    input  wire         i_clk,
    input  wire         i_rst_n,

    input  wire         i_valid,
    input  wire [255:0] i_acc,

    output reg          o_valid,
    output reg  [63:0]  o_data
);

    // =========================================================================
    // Per-lane intermediate buses
    // =========================================================================

    wire [255:0] rounded_bus;
    wire [63:0]  saturated_bus;
    wire [63:0]  relu_bus;

    // =========================================================================
    // Eight parallel quantization/ReLU lanes
    // =========================================================================

    genvar lane;

    generate

        for (lane = 0; lane < 8; lane = lane + 1) begin : GEN_QUANT_LANE

            // -----------------------------------------------------------------
            // Round-to-nearest-even:
            //
            // signed INT32 Q8.8
            // -> signed INT32 at Q4.4 scale
            // -----------------------------------------------------------------

            round_shift_even u_round_shift_even (
                .i_value (
                    i_acc[(lane*32) +: 32]
                ),

                .o_value (
                    rounded_bus[(lane*32) +: 32]
                )
            );

            // -----------------------------------------------------------------
            // Saturate:
            //
            // signed INT32 -> signed INT8 [-128,127]
            // -----------------------------------------------------------------

            sat_int8 u_sat_int8 (
                .i_value (
                    rounded_bus[(lane*32) +: 32]
                ),

                .o_value (
                    saturated_bus[(lane*8) +: 8]
                )
            );

            // -----------------------------------------------------------------
            // ReLU:
            //
            // signed INT8 -> max(value, 0)
            // -----------------------------------------------------------------

            relu_int8 u_relu_int8 (
                .i_value (
                    saturated_bus[(lane*8) +: 8]
                ),

                .o_value (
                    relu_bus[(lane*8) +: 8]
                )
            );

        end

    endgenerate

    // =========================================================================
    // Registered output
    // =========================================================================

    always @(posedge i_clk) begin

        if (!i_rst_n) begin

            o_valid <= 1'b0;
            o_data  <= 64'd0;

        end
        else begin

            o_valid <= i_valid;

            if (i_valid) begin

                o_data <= relu_bus;

            end

        end

    end

endmodule

`default_nettype wire

