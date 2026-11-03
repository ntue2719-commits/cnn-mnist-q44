`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 10:15:15 PM
// Design Name: Nguyen Tri Tue
// Module Name: pool2
//
// Description:
//   Signed 2x2 max-pooling datapath for 8 channels.
//
// Input:
//   256-bit pooling window.
//
// Packing:
//
//   o_window source positions:
//
//     A B
//     C D
//
//   i_window[63:0]    = A
//   i_window[127:64]  = B
//   i_window[191:128] = C
//   i_window[255:192] = D
//
// Each spatial position contains:
//
//   [7:0]   ch0
//   [15:8]  ch1
//   ...
//   [63:56] ch7
//
// Output:
//   64-bit pooled result:
//
//   o_data[7:0]   = ch0
//   ...
//   o_data[63:56] = ch7
//
// Operation:
//   out[c] = max(A[c], B[c], C[c], D[c])
//
// IMPORTANT:
//   Signed INT8 comparison.
//   No rounding.
//   No saturation.
//   No ReLU.
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module pool2 (
    input  wire         i_clk,
    input  wire         i_rst_n,

    input  wire         i_valid,
    input  wire [255:0] i_window,

    output reg          o_valid,
    output reg  [63:0]  o_data
);

    // =========================================================================
    // Channel values
    // =========================================================================

    wire signed [7:0] a [0:7];
    wire signed [7:0] b [0:7];
    wire signed [7:0] c [0:7];
    wire signed [7:0] d [0:7];

    reg signed [7:0] max_value [0:7];

    genvar ch;

    generate
        for (ch = 0; ch < 8; ch = ch + 1) begin : GEN_INPUT_UNPACK

            assign a[ch] =
                $signed(i_window[(ch*8) +: 8]);

            assign b[ch] =
                $signed(i_window[64 + (ch*8) +: 8]);

            assign c[ch] =
                $signed(i_window[128 + (ch*8) +: 8]);

            assign d[ch] =
                $signed(i_window[192 + (ch*8) +: 8]);

        end
    endgenerate

    // =========================================================================
    // Combinational signed maximum
    // =========================================================================

    integer i;

    always @(*) begin

        for (i = 0; i < 8; i = i + 1) begin

            max_value[i] = a[i];

            if (b[i] > max_value[i])
                max_value[i] = b[i];

            if (c[i] > max_value[i])
                max_value[i] = c[i];

            if (d[i] > max_value[i])
                max_value[i] = d[i];

        end

    end

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

                o_data[7:0]   <= max_value[0];
                o_data[15:8]  <= max_value[1];
                o_data[23:16] <= max_value[2];
                o_data[31:24] <= max_value[3];

                o_data[39:32] <= max_value[4];
                o_data[47:40] <= max_value[5];
                o_data[55:48] <= max_value[6];
                o_data[63:56] <= max_value[7];

            end

        end

    end

endmodule

`default_nettype wire