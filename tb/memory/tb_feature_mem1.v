`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 09:31:19 PM
// Design Name: 
// Module Name: tb_feature_mem1
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////

`timescale 1ns / 1ps
`default_nettype none
module tb_feature_mem1;
    reg         i_clk = 1'b0;
    always #5 i_clk = ~i_clk;
    reg         i_rst_n = 1'b1;
    reg         i_start = 1'b0;
    reg         i_wr_valid = 1'b0;
    reg  [31:0] i_wr_data = 32'd0;
    reg         i_start_rd = 1'b0;
    wire        o_rd_valid;
    wire [31:0] o_rd_data;

    feature_mem1 dut (
        .i_clk(i_clk), .i_rst_n(i_rst_n), .i_start(i_start),
        .i_wr_valid(i_wr_valid), .i_wr_data(i_wr_data),
        .i_start_rd(i_start_rd), .o_rd_valid(o_rd_valid),
        .o_rd_data(o_rd_data)
    );

    reg [31:0] expected [0:168];
    integer errors = 0;
    integer checks = 0;
    integer i;
    integer pass_count = 0;

    function [31:0] pattern;
        input integer idx;
        begin
            // Deliberately different byte lanes and varying positions.
            pattern = {8'hC3 ^ idx[7:0], 8'h5A + idx[7:0],
                       8'h80 - idx[7:0], idx[7:0]};
        end
    endfunction

    // Inputs applied at falling edge; output checked after rising edge.
    task tick;
        input rstn;
        input start_wr;
        input wr_v;
        input [31:0] wr_d;
        input start_rd;
        input expect_valid;
        input check_data;
        input [31:0] expect_data;
        begin
            @(negedge i_clk);
            i_rst_n = rstn;
            i_start = start_wr;
            i_wr_valid = wr_v;
            i_wr_data = wr_d;
            i_start_rd = start_rd;
            @(posedge i_clk); #1;
            checks = checks + 1;
            if (o_rd_valid !== expect_valid) begin
                $display("FAIL cycle=%0d: rd_valid expected=%b actual=%b",
                         checks, expect_valid, o_rd_valid);
                errors = errors + 1;
            end
            if (check_data && o_rd_data !== expect_data) begin
                $display("FAIL cycle=%0d: rd_data expected=%08h actual=%08h",
                         checks, expect_data, o_rd_data);
                errors = errors + 1;
            end
        end
    endtask

    task read_full_and_check;
        integer k;
        begin
            // Request/setup cycle: must NOT output mem[0] yet.
            tick(1, 0, 0, 32'd0, 1, 0, 0, 32'd0);
            for (k=0; k<169; k=k+1)
                tick(1, 0, 0, 32'd0, 0, 1, 1, expected[k]);
            // Exactly 169 reads, no extra word or pointer wrap.
            tick(1, 0, 0, 32'd0, 0, 0, 0, 32'd0);
            tick(1, 0, 0, 32'd0, 0, 0, 0, 32'd0);
            pass_count = pass_count + 1;
        end
    endtask

    initial begin
        $display("FEATURE_MEM1 SELF-CHECKING TEST START");
        // Reset without assuming BRAM is cleared.
        tick(0, 0, 0, 32'd0, 0, 0, 1, 32'd0);
        // Combined start+valid must write word 0 in same cycle.
        expected[0] = pattern(0);
        tick(1, 1, 1, expected[0], 0, 0, 0, 32'd0);
        for (i=1; i<169; i=i+1) begin
            expected[i] = pattern(i);
            if ((i % 11) == 0) // injected write-valid gap
                tick(1, 0, 0, 32'hDEADBEEF, 0, 0, 0, 32'd0);
            tick(1, 0, 1, expected[i], 0, 0, 0, 32'd0);
        end
        // Out-of-range writes must be discarded, even with long bursts.
        for (i=0; i<5; i=i+1)
            tick(1, 0, 1, 32'hBAD00000 + i, 0, 0, 0, 32'd0);
        $display("CASE 1: full write, gaps, overflow and full read");
        read_full_and_check();
        $display("CASE 2: repeat full read without rewriting");
        read_full_and_check();

        // Interrupted read: a new read request restarts at address zero.
        $display("CASE 3: read restart");
        tick(1, 0, 0, 0, 1, 0, 0, 0);
        for (i=0; i<7; i=i+1)
            tick(1, 0, 0, 0, 0, 1, 1, expected[i]);
        read_full_and_check();

        // Synchronous reset while reading aborts transaction, not RAM contents.
        $display("CASE 4: reset in the middle of reading");
        tick(1, 0, 0, 0, 1, 0, 0, 0);
        tick(1, 0, 0, 0, 0, 1, 1, expected[0]);
        tick(0, 0, 0, 0, 0, 0, 1, 0);
        tick(1, 0, 0, 0, 0, 0, 0, 0);
        read_full_and_check();

        // Start anew and overwrite the complete memory, proving reusability.
        $display("CASE 5: rewrite second frame");
        tick(1, 1, 0, 0, 0, 0, 0, 0);
        for (i=0; i<169; i=i+1) begin
            expected[i] = ~pattern(i);
            tick(1, 0, 1, expected[i], 0, 0, 0, 0);
        end
        read_full_and_check();

        if (errors == 0) begin
            $display("FEATURE_MEM1 TEST: PASS (checks=%0d, full_reads=%0d)",
                     checks, pass_count);
        end else begin
            $display("FEATURE_MEM1 TEST: FAIL (errors=%0d, checks=%0d)",
                     errors, checks);
            $fatal(1, "Feature Mem1 verification failed");
        end
        $finish;
    end
endmodule
`default_nettype wire
