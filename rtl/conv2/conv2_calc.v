`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 09:58:34 PM
// Design Name: Nguyen Tri Tue
// Module Name: conv2_calc
//
// Description:
//   Phase-1 Conv2 MAC datapath.
//
// Input window:
//   4 input channels x 3x3 x INT8 = 36 bytes = 288 bits
//
// Window packing:
//   ch0 -> i_window[71:0]
//   ch1 -> i_window[143:72]
//   ch2 -> i_window[215:144]
//   ch3 -> i_window[287:216]
//
// Within each channel:
//   P0 -> lowest byte
//   ...
//   P8 -> highest byte
//
// Weights:
//   8 output channels x 4 input channels x 3x3
//   = 288 signed INT8 weights
//
// Weight flatten order:
//   [out_channel][in_channel][ky][kx]
//
// Biases:
//   8 signed INT8 Q4.4 biases
//
// Arithmetic:
//   acc[oc] = (signed_bias[oc] << 4)
//           + sum(input * weight)
//
// Output:
//   8 x signed INT32 Q8.8 raw accumulators
//
// Packing:
//   o_acc[31:0]    = output channel 0
//   o_acc[63:32]   = output channel 1
//   ...
//   o_acc[255:224] = output channel 7
//
// IMPORTANT:
//   No rounding.
//   No saturation.
//   No ReLU.
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module conv2_calc (
    input  wire          i_clk,
    input  wire          i_rst_n,

    input  wire          i_valid,

    input  wire [287:0]  i_window,
    input  wire [2303:0] i_weights,
    input  wire [63:0]   i_biases,

    output reg           o_valid,
    output reg  [255:0]  o_acc
);

    // =========================================================================
    // Combinational MAC temporaries
    // =========================================================================

    reg signed [31:0] acc_temp [0:7];

    reg signed [7:0]  input_temp;
    reg signed [7:0]  weight_temp;
    reg signed [15:0] product_temp;

    reg signed [31:0] bias_temp;

    integer oc;
    integer ic;
    integer ky;
    integer kx;

    integer window_byte_index;
    integer weight_byte_index;

    // =========================================================================
    // Combinational accumulation
    // =========================================================================

    always @(*) begin

        // Defaults for procedural temporaries.
        input_temp  = 8'sd0;
        weight_temp = 8'sd0;
        product_temp = 16'sd0;
        bias_temp    = 32'sd0;

        for (oc = 0; oc < 8; oc = oc + 1) begin

            // -----------------------------------------------------------------
            // Sign-extend INT8 bias to INT32 BEFORE shifting.
            //
            // Bias is Q4.4.
            // Accumulator is Q8.8.
            // Therefore align by << 4.
            // -----------------------------------------------------------------

            bias_temp = {
                {24{i_biases[(oc*8) + 7]}},
                i_biases[(oc*8) +: 8]
            };

            acc_temp[oc] = bias_temp <<< 4;

            // -----------------------------------------------------------------
            // Sum 4 x 3 x 3 = 36 signed products.
            // -----------------------------------------------------------------

            for (ic = 0; ic < 4; ic = ic + 1) begin

                for (ky = 0; ky < 3; ky = ky + 1) begin

                    for (kx = 0; kx < 3; kx = kx + 1) begin

                        // Input window byte:
                        //
                        // [ic][ky][kx]
                        window_byte_index =
                            (ic * 9)
                            +
                            (ky * 3)
                            +
                            kx;

                        // Weight byte:
                        //
                        // [oc][ic][ky][kx]
                        weight_byte_index =
                            (oc * 36)
                            +
                            (ic * 9)
                            +
                            (ky * 3)
                            +
                            kx;

                        input_temp =
                            $signed(
                                i_window[
                                    (window_byte_index * 8) +: 8
                                ]
                            );

                        weight_temp =
                            $signed(
                                i_weights[
                                    (weight_byte_index * 8) +: 8
                                ]
                            );

                        // Explicit 16-bit signed product prevents
                        // accidental 8-bit result truncation.
                        product_temp =
                            input_temp * weight_temp;

                        acc_temp[oc] =
                            acc_temp[oc]
                            +
                            product_temp;

                    end

                end

            end

        end

    end

    // =========================================================================
    // Registered outputs
    // =========================================================================

    always @(posedge i_clk) begin

        if (!i_rst_n) begin

            o_valid <= 1'b0;
            o_acc   <= 256'd0;

        end
        else begin

            o_valid <= i_valid;

            if (i_valid) begin

                o_acc[31:0]    <= acc_temp[0];
                o_acc[63:32]   <= acc_temp[1];
                o_acc[95:64]   <= acc_temp[2];
                o_acc[127:96]  <= acc_temp[3];

                o_acc[159:128] <= acc_temp[4];
                o_acc[191:160] <= acc_temp[5];
                o_acc[223:192] <= acc_temp[6];
                o_acc[255:224] <= acc_temp[7];

            end

        end

    end

endmodule

`default_nettype wire