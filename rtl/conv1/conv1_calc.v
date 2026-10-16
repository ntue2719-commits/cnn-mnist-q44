`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/10/2026 09:32:26 AM
// Design Name: Nguyen Tri Tue
// Module Name: conv1_calc
// Description:
//   Conv1 MAC datapath.
//
//   Input:
//     - 3x3 window: 9 signed INT8 Q4.4 pixels
//     - 4x9 weights: 36 signed INT8 Q4.4 values
//     - 4 signed INT8 Q4.4 biases
//
//   Output:
//     - 4 signed INT32 Q8.8 accumulators
//
//   Formula:
//     acc[oc] = (bias[oc] << 4)
//             + sum(pixel[k] * weight[oc][k])
//
//   Latency:
//     i_valid at cycle N
//     o_valid at cycle N+1
//////////////////////////////////////////////////////////////////////////////////
`default_nettype none

module conv1_calc (
    input wire          i_clk,
    input wire          i_rst_n,

    input wire          i_valid,
    input wire [71:0]   i_window,
    input wire [287:0]  i_weights,
    input wire [31:0]   i_biases,

    output reg          o_valid,
    output reg [127:0]  o_acc
);
    integer oc; //output channel
    integer k; //position in kernel

    reg signed [31:0] acc_tmp [0:3];

    reg signed [7:0] pixel_tmp;
    reg signed [7:0] weight_tmp;
    reg signed [7:0] bias_tmp;

    reg signed [15:0] product_tmp;

    //Combinational MAC

    always @(*) begin

        //Default
        for (oc = 0; oc < 4; oc = oc + 1)
            acc_tmp[oc] = 32'sd0;

        pixel_tmp = 8'sd0;
        weight_tmp = 8'sd0;
        bias_tmp = 8'sd0;
        product_tmp = 16'sd0;

        //Four output channels
        for (oc =0; oc < 4; oc = oc + 1) begin
            
            //bias Q4.4 -> Q8.8
            bias_tmp = $signed(i_biases[oc*8+: 8]);

            //Explicit sign extension before shifting
            acc_tmp[oc] = $signed({{24{bias_tmp[7]}},bias_tmp});
            acc_tmp[oc] = acc_tmp[oc] <<< 4;

            //3x3 MAC = 9 multiplication

            for (k=0; k<9; k=k+1) begin
                
                pixel_tmp = $signed(i_window[k*8 +: 8]);

                weight_tmp = $signed(i_weights[(oc*9+k)*8 +: 8]);

                product_tmp = pixel_tmp * weight_tmp;

                acc_tmp[oc] = acc_tmp[oc] + $signed({{16{product_tmp[15]}},product_tmp});

            end
        end
    end

    //Output registers

    always @(posedge i_clk) begin
        if(!i_rst_n) begin
            o_valid <= 1'b0;
            o_acc <= 128'b0;
        end
        else begin
            o_valid <= i_valid;
            if(i_valid) begin
                o_acc[31:0] <= acc_tmp[0];
                o_acc[63:32] <= acc_tmp[1];
                o_acc[95:64] <= acc_tmp[2];
                o_acc[127:96] <= acc_tmp[3];
            end
        end
    end

endmodule

`default_nettype wire