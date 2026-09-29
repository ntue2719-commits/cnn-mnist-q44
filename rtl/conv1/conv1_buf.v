module conv1_buf (
    input clk, input rst_n, input valid_in,
    input [7:0] pixel_in,
    output valid_out, output [71:0] window_out
);
    // TODO: 28x28 stream -> 3x3 sliding window.
endmodule
