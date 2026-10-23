`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 10:32:09 PM
// Design Name: Nguyen Tri Tue
// Module Name: conv2_rom
//
// Description:
//   Phase-1 Conv2 parameter ROM.
//
// Parameters:
//   Conv2 weights:
//     shape = [8][4][3][3]
//     total = 288 x signed INT8 Q4.4
//
//   Conv2 biases:
//     shape = [8]
//     total = 8 x signed INT8 Q4.4
//
// Weight file order:
//   [out_channel][in_channel][ky][kx]
//
// Flatten:
//   index = oc*36 + ic*9 + ky*3 + kx
//
// Bus packing:
//
//   weight[0]   -> o_weights[7:0]
//   weight[1]   -> o_weights[15:8]
//   ...
//   weight[287] -> o_weights[2303:2296]
//
//   bias[0] -> o_biases[7:0]
//   ...
//   bias[7] -> o_biases[63:56]
//
// Phase-1 contract:
//   - $readmemh initialization
//   - combinational/stable output buses
//   - no clock
//   - no reset
//   - no hidden latency
//
// NOTE:
//   Override WEIGHT_FILE / BIAS_FILE if Vivado simulation cannot locate
//   the default relative filenames.
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module conv2_rom #(
    parameter WEIGHT_FILE = "conv2_weight.hex",
    parameter BIAS_FILE   = "conv2_bias.hex"
)(
    output wire [2303:0] o_weights,
    output wire [63:0]   o_biases
);

    // =========================================================================
    // Parameter memories
    // =========================================================================

    reg [7:0] weight_mem [0:287];
    reg [7:0] bias_mem   [0:7];

    // =========================================================================
    // File initialization
    // =========================================================================

    initial begin

        $readmemh(
            WEIGHT_FILE,
            weight_mem
        );

        $readmemh(
            BIAS_FILE,
            bias_mem
        );

    end

    // =========================================================================
    // Flatten parameter memories onto stable output buses
    // =========================================================================

    genvar w;

    generate

        for (w = 0; w < 288; w = w + 1) begin : GEN_WEIGHT_BUS

            assign o_weights[
                (w * 8) +: 8
            ] = weight_mem[w];

        end

    endgenerate


    genvar b;

    generate

        for (b = 0; b < 8; b = b + 1) begin : GEN_BIAS_BUS

            assign o_biases[
                (b * 8) +: 8
            ] = bias_mem[b];

        end

    endgenerate

endmodule

`default_nettype wire