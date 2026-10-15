`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/08/2026 11:42:41 AM
// Design Name: Nguyen Tri Tue
// Module Name: relu_int8
// Description:
//   Applies ReLU activation to a signed INT8 input.
//   Negative values are clamped to zero.
//   Non-negative values pass through unchanged.
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none
module relu_int8 (
    input  signed [7:0] i_value,
    output signed [7:0] o_value
);
    assign o_value = (i_value < 0) ? 8'sd0 : i_value;
endmodule

`default_nettype wire
