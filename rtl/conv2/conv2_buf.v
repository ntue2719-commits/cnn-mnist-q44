module conv2_buf (
    input clk, input rst_n, input valid_in,
    input [31:0] data_in,
    output valid_out, output [287:0] window_out
);
    // TODO: 4-channel stream -> 4x3x3 window.
endmodule
