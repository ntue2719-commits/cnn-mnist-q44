module pool1_buf (
    input clk, input rst_n, input valid_in,
    input [31:0] data_in,
    output valid_out, output [127:0] pool_window
);
    // TODO: 4-channel 2x2 buffer, stride 2.
endmodule
