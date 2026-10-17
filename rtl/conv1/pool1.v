`timescale 1ns / 1ps

//////////////////////////////////////////////////////////////////////////////////
// Design Name: Nguyen Tri Tue
// Module Name: pool1
// Description:
//   Signed 2x2 max-pooling datapath for 4 channels.
//
//   Input:
//     - 128-bit pooling window
//     - 4 spatial positions: A, B, C, D
//     - each position contains 4 signed INT8 Q4.4 channels
//
//   Output:
//     - 32-bit pooled result
//     - 4 signed INT8 Q4.4 channels
//
//   Operation:
//     out[c] = max(A[c], B[c], C[c], D[c])
//
//   No rounding, saturation, or ReLU is performed here.
/////////////////////////////////////////////////////////////////////////////   /////

`default_nettype none

module pool1 (
    input  wire         i_clk,
    input  wire         i_rst_n,

    input  wire         i_valid,
    input  wire [127:0] i_window,

    output reg          o_valid,
    output reg  [31:0]  o_data
);

    // -------------------------------------------------------------------------
    // Channel 0
    // -------------------------------------------------------------------------

    wire signed [7:0] a0;
    wire signed [7:0] b0;
    wire signed [7:0] c0;
    wire signed [7:0] d0;

    reg signed [7:0] max0;

    assign a0 = $signed(i_window[7:0]);
    assign b0 = $signed(i_window[39:32]);
    assign c0 = $signed(i_window[71:64]);
    assign d0 = $signed(i_window[103:96]);


    // -------------------------------------------------------------------------
    // Channel 1
    // -------------------------------------------------------------------------

    wire signed [7:0] a1;
    wire signed [7:0] b1;
    wire signed [7:0] c1;
    wire signed [7:0] d1;

    reg signed [7:0] max1;

    assign a1 = $signed(i_window[15:8]);
    assign b1 = $signed(i_window[47:40]);
    assign c1 = $signed(i_window[79:72]);
    assign d1 = $signed(i_window[111:104]);


    // -------------------------------------------------------------------------
    // Channel 2
    // -------------------------------------------------------------------------

    wire signed [7:0] a2;
    wire signed [7:0] b2;
    wire signed [7:0] c2;
    wire signed [7:0] d2;

    reg signed [7:0] max2;

    assign a2 = $signed(i_window[23:16]);
    assign b2 = $signed(i_window[55:48]);
    assign c2 = $signed(i_window[87:80]);
    assign d2 = $signed(i_window[119:112]);


    // -------------------------------------------------------------------------
    // Channel 3
    // -------------------------------------------------------------------------

    wire signed [7:0] a3;
    wire signed [7:0] b3;
    wire signed [7:0] c3;
    wire signed [7:0] d3;

    reg signed [7:0] max3;

    assign a3 = $signed(i_window[31:24]);
    assign b3 = $signed(i_window[63:56]);
    assign c3 = $signed(i_window[95:88]);
    assign d3 = $signed(i_window[127:120]);


    // -------------------------------------------------------------------------
    // Combinational signed max
    // -------------------------------------------------------------------------

    always @(*) begin

        // Channel 0
        max0 = a0;

        if (b0 > max0)
            max0 = b0;

        if (c0 > max0)
            max0 = c0;

        if (d0 > max0)
            max0 = d0;


        // Channel 1
        max1 = a1;

        if (b1 > max1)
            max1 = b1;

        if (c1 > max1)
            max1 = c1;

        if (d1 > max1)
            max1 = d1;


        // Channel 2
        max2 = a2;

        if (b2 > max2)
            max2 = b2;

        if (c2 > max2)
            max2 = c2;

        if (d2 > max2)
            max2 = d2;


        // Channel 3
        max3 = a3;

        if (b3 > max3)
            max3 = b3;

        if (c3 > max3)
            max3 = c3;

        if (d3 > max3)
            max3 = d3;

    end


    // -------------------------------------------------------------------------
    // Registered output
    // -------------------------------------------------------------------------

    always @(posedge i_clk) begin
        if (!i_rst_n) begin

            o_valid <= 1'b0;
            o_data  <= 32'd0;

        end
        else begin

            o_valid <= i_valid;

            if (i_valid) begin

                o_data[7:0]   <= max0;
                o_data[15:8]  <= max1;
                o_data[23:16] <= max2;
                o_data[31:24] <= max3;

            end
        end
    end

endmodule

`default_nettype wire