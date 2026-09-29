module cnn_mnist_top (
    input clk, input rst_n, input start, input valid_in,
    input [7:0] pixel_in,
    output busy, output done, output valid_out,
    output [3:0] digit_out
);
    // TODO: top-level CNN transaction controller.
endmodule
