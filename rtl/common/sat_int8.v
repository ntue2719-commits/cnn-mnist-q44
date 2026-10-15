`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/08/2026 12:13:47 PM
// Design Name: 
// Module Name: sat_int8
// Description:
//   Saturates a signed 32-bit value to signed INT8 range [-128, 127].
//////////////////////////////////////////////////////////////////////////////////
`default_nettype none

module sat_int8(
    input wire signed [31:0]   i_value,
    output wire signed [7:0] o_value
    );
    
    assign o_value = (i_value > 32'sd127)  ?  8'sd127 :
                     (i_value < -32'sd128) ? -8'sd128 : i_value[7:0];
    
endmodule
`default_nettype wire