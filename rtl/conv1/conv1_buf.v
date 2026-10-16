`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/09/2026 11:06:19 PM
// Design Name: Nguyen Tri Tue
// Module Name: conv1_buf
// Description:
//   Streaming 3x3 window generator for a 28x28 single-channel image.
//
//   Input:
//     - 784 signed INT8 Q4.4 pixels in row-major order.
//     - Counters advance only when i_valid = 1.
//
//   Output:
//     - 26x26 = 676 valid 3x3 windows.
//     - Window packing:
//         P0 P1 P2
//         P3 P4 P5
//         P6 P7 P8
//
//       o_window[7:0]   = P0
//       o_window[15:8]  = P1
//       ...
//       o_window[71:64] = P8
//////////////////////////////////////////////////////////////////////////////////
`default_nettype none
module conv1_buf (
    input wire          i_clk,
    input wire          i_rst_n,
    input wire          i_start,
    
    input wire          i_valid,
    input wire [7:0]   i_pixel

    output reg          o_valid,
    output reg [71:0]   o_window
);

    //Pixel position
    reg [4:0] x_count;
    reg [4:0] y_count;

    // Two line buffer
    // line_buf1[x] = pixel1 from y-1
    // line_buf2[x] = pixel2 from y-2

    reg [7:0] line_buf1 [0:127];
    reg [7:0] line_buf2 [0:127];

    //Horizontal delay registers
    // d1 = previous column
    // d2 = two column before current

    reg [7:0] top_d1;
    reg [7:0] top_d2;

    reg [7:0] mid_d1;
    reg [7:0] mid_d2;

    reg [7:0] bot_d1;
    reg [7:0] bot_d2;

    //Main sequential logic
    always @(posedge i_clk) begin
    if(!i_rst_n) begin

        x_count <= 5'd0;
        y_count <= 5'd0;

        top_d1 <= 8'd0;
        top_d2 <= 8'd0;

        mid_d1 <= 8'd0;
        mid_d2 <= 8'd0;

        bot_d1 <= 8'd0;
        bot_d2 <= 8'd0;

        o_valid <= 1'b0;
        o_window <= 72'd0;
    end

    else if (i_start) begin

        x_count <= 5'd0;
        y_count <= 5'd0;

        top_d1 <= 8'd0;
        top_d2 <= 8'd0;

        mid_d1 <= 8'd0;
        mid_d2 <= 8'd0;

        bot_d1 <= 8'd0;
        bot_d2 <= 8'd0;

        o_valid <= 1'b0;
        o_window <= 72'd0;
    end

    else begin
        //default no output transaction
        o_valid <= 1'b0;

        if(i_valid) begin

            //generate valid 3x3 window
            //need at least:
            //row >= 2
            //col >= 2

            if((y_count >= 5'd2) && (x_count >= 5'd2)) begin
                o_valid <= 1'b1;

                // P0 = top-left
                // ...
                // P8 = bottom-right/current pixel
            o_window <= {
                i_pixel, bot_d1, bot_d2,  //P8, P7, P6

                line_buf1[x_count], mid_d1, mid_d2,  //P5, P4, P3

                line_buf2[x_count], top_d1, top_d2 //P2, P1, P0
            };
        end

        //Horizontal shift registers (window 3x3)

        top_d2 <= top_d1;
        top_d1 <= line_buf2[x_count];

        mid_d2 <= mid_d1;
        mid_d1 <= line_buf1[x_count];

        bot_d2 <= bot_d1;
        bot_d1 <= i_pixel;

        //Vertical line-buffer update
        //Old line_buf1 -> line_buf2
        //Current pixel -> line_buf1

        line_buf2[x_count] <= line_buf1[x_count];
        line_buf1[x_count] <= i_pixel;

        //Advance coordinates only on accepted pixels

        if(x_count == 5'd27) begin
            x_count <= 5'd0;

            if(y_count == 5'd27)  
                y_count <= 5'd0;
            else
                y_count <= y_count + 5'd1;
                // Horizontal state does not need explicit clearing:
                // x=0 and x=1 overwrite stale values before the next
                // valid window at x=2.                
        end
        else begin
            x_count <= x_count + 5'd1;
            end
        end
    end
end
endmodule
`default_nettype wire
