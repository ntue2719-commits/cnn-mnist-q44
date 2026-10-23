`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 09:30:09 PM
// Design Name: 
// Module Name: feature_mem1
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// Feature Mem1: 169 x 32-bit canonical channel-major byte stream.
// No reordering occurs here: data is already packed by pool1_reorder.
// Active-low synchronous reset clears control state, not RAM contents.
// One accepted write per i_wr_valid, including i_start + i_wr_valid.
// A read start is a setup cycle; the FIRST valid word appears on the
// following rising edge. Exactly 169 words are emitted per read request.
// Do not assert i_start_rd concurrently with writes (undefined read/write
// collision behavior is intentionally excluded from the Phase-1 interface).


`default_nettype none
module feature_mem1 (
    input  wire        i_clk,
    input  wire        i_rst_n,
    input  wire        i_start,
    input  wire        i_wr_valid,
    input  wire [31:0] i_wr_data,
    input  wire        i_start_rd,
    output reg         o_rd_valid,
    output reg  [31:0] o_rd_data
);
    localparam [7:0] DEPTH = 8'd169;
    reg [31:0] mem [0:168];
    reg [7:0] wr_ptr;
    reg [7:0] rd_ptr;
    reg       rd_active;

    // Write pointer saturates at 169; there is no wraparound.
    // i_start combined with i_wr_valid writes address zero.
    always @(posedge i_clk) begin
        if (!i_rst_n) begin
            wr_ptr <= 8'd0;
        end else if (i_start) begin
            if (i_wr_valid) begin
                mem[0] <= i_wr_data;
                wr_ptr <= 8'd1;
            end else begin
                wr_ptr <= 8'd0;
            end
        end else if (i_wr_valid && wr_ptr < DEPTH) begin
            mem[wr_ptr] <= i_wr_data;
            wr_ptr <= wr_ptr + 8'd1;
        end
    end

    // Registered synchronous sequential read.
    // i_start_rd has priority and always creates a one-cycle setup gap.
    always @(posedge i_clk) begin
        if (!i_rst_n) begin
            rd_ptr     <= 8'd0;
            rd_active  <= 1'b0;
            o_rd_valid <= 1'b0;
            o_rd_data  <= 32'd0;
        end else if (i_start_rd) begin
            rd_ptr     <= 8'd0;
            rd_active  <= 1'b1;
            o_rd_valid <= 1'b0;
        end else if (rd_active) begin
            o_rd_data  <= mem[rd_ptr];
            o_rd_valid <= 1'b1;
            if (rd_ptr == 8'd168) begin
                rd_active <= 1'b0;
                rd_ptr    <= DEPTH;
            end else begin
                rd_ptr    <= rd_ptr + 8'd1;
            end
        end else begin
            o_rd_valid <= 1'b0;
        end
    end
endmodule

`default_nettype wire

