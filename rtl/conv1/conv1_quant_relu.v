`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/15/2026 10:24:09 PM
// Design Name: 
// Module Name: conv1_quant_relu
// Description:
//   Quantization and ReLU stage for Conv1.
//
// Input:
//   Signed INT32 accumulator with 8 fractional bits
//   (scale = 2^8).
//
// Operation:
//   Round-to-nearest-even while shifting right by 4 bits.
//
// Output:
//   Signed INT32 integer with 4 fractional bits
//   (scale = 2^4).
//
// Note:
//   Output remains 32-bit. Saturation to signed INT8
//   Q4.4 is performed by sat_int8.
//
//   Packing:
//     i_acc[31:0]    = channel 0
//     i_acc[63:32]   = channel 1
//     i_acc[95:64]   = channel 2
//     i_acc[127:96]  = channel 3
//
//     o_data[7:0]    = channel 0
//     o_data[15:8]   = channel 1
//     o_data[23:16]  = channel 2
//     o_data[31:24]  = channel 3
//
//   Latency:
//     i_valid at cycle N
//     o_valid + o_data at cycle N+1
//////////////////////////////////////////////////////////////////////////////////
`default_nettype none
module conv1_quant_relu (
    input wire          i_clk,
    input wire          i_rst_n,
    
    input wire          i_valid,
    input wire [127:0]  i_acc,

    output reg          o_valid,
    output reg [31:0]   o_data
);
     
    //Channel 0, round-even -> saturation -> ReLU.

    wire signed [31:0] acc_ch0;
    wire signed [31:0] round_ch0;
    wire signed [7:0] sat_ch0;
    wire signed [7:0] relu_ch0;

    assign acc_ch0 = $signed(i_acc[31:0]);

    round_shift_even u_round_ch0 (
        .i_value (acc_ch0),
        .o_value (round_ch0)
    );

    sat_int8 u_sat_ch0 (
        .i_value (round_ch0),
        .o_value (sat_ch0)
    );

    relu_int8 u_relu_ch0 (
        .i_value (sat_ch0),
        .o_value (relu_ch0)
    );

    //Channel 1

    wire signed [31:0] acc_ch1;
    wire signed [31:0] round_ch1;
    wire signed [7:0]  sat_ch1;
    wire signed [7:0]  relu_ch1;

    assign acc_ch1 = $signed(i_acc[63:32]);

    round_shift_even u_round_ch1 (
        .i_value (acc_ch1),
        .o_value (round_ch1)
    );

    sat_int8 u_sat_ch1 (
        .i_value (round_ch1),
        .o_value (sat_ch1)
    );
	
    relu_int8 u_relu_ch1 (
        .i_value (sat_ch1),
        .o_value (relu_ch1)
    );

    //Channel 2

    wire signed [31:0] acc_ch2;
    wire signed [31:0] round_ch2;
    wire signed [7:0]  sat_ch2;
    wire signed [7:0]  relu_ch2;

    assign acc_ch2 = $signed(i_acc[95:64]);

    round_shift_even u_round_ch2 (
        .i_value (acc_ch2),
        .o_value (round_ch2)
    );

    sat_int8 u_sat_ch2 (
        .i_value (round_ch2),
        .o_value (sat_ch2)
    );

    relu_int8 u_relu_ch2 (
        .i_value (sat_ch2),
        .o_value (relu_ch2)
    );

    //Channel 3

    wire signed [31:0] acc_ch3;
    wire signed [31:0] round_ch3;
    wire signed [7:0]  sat_ch3;
    wire signed [7:0]  relu_ch3;

    assign acc_ch3 = $signed(i_acc[127:96]);

    round_shift_even u_round_ch3 (
        .i_value (acc_ch3),
        .o_value (round_ch3)
    );

    sat_int8 u_sat_ch3 (
        .i_value (round_ch3),
        .o_value (sat_ch3)
    );

    relu_int8 u_relu_ch3 (
        .i_value (sat_ch3),
        .o_value (relu_ch3)
    );

    //Register ouptut

    always @ (posedge i_clk) begin
        if(!i_rst_n) begin
            o_valid <= 1'b0;
            o_data <= 32'd0;
        end
        else begin
            o_valid <= i_valid;

            if(i_valid) begin
                o_data[7:0] <= relu_ch0;
                o_data[15:8] <= relu_ch1;
                o_data[23:16] <= relu_ch2;
                o_data[31:24] <= relu_ch3;
            end
        end
    end
endmodule
`default_nettype wire
