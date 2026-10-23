`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/09/2026 11:07:45 PM
// Design Name: 
// Module Name: tb_conv1_buf
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

`default_nettype none

module tb_conv1_buf;

    reg         clk;
    reg         rst_n;
    reg         start;
    reg         valid;
    reg  [7:0]  pixel;
    wire        o_valid;
    wire [71:0] o_window;

    conv1_buf dut (
        .i_clk   (clk),
        .i_rst_n (rst_n),
        .i_start (start),
        .i_valid (valid),
        .i_pixel (pixel),
        .o_valid (o_valid),
        .o_window(o_window)
    );

    initial clk = 1'b0;
    always #5 clk = ~clk;

    reg [7:0] img [0:783];

    integer win_count;
    integer err_count;
    integer k, r, c, dy, dx, n;
    reg [71:0] expected;

    // Expected window k
    task calc_expected;
        input integer idx;
        integer rr, cc, ddy, ddx, p;
        begin
            rr = idx / 26;
            cc = idx % 26;
            expected = 72'd0;
            for (ddy = 0; ddy < 3; ddy = ddy + 1)
                for (ddx = 0; ddx < 3; ddx = ddx + 1) begin
                    p = ddy*3 + ddx;
                    expected[p*8 +: 8] = img[(rr+ddy)*28 + (cc+ddx)];
                end
        end
    endtask

    // Monitor
    always @(posedge clk) begin
        if (rst_n && o_valid) begin
            if (win_count < 676) begin
                calc_expected(win_count);
                if (o_window !== expected) begin
                    err_count = err_count + 1;
                    if (err_count <= 10)
                        $display("MISMATCH win=%0d (r=%0d,c=%0d) got=%h exp=%h",
                                 win_count, win_count/26, win_count%26,
                                 o_window, expected);
                end
                // Print first / middle / last
                if (win_count == 0 || win_count == 338 || win_count == 675)
                    $display("win %0d: %h", win_count, o_window);
            end
            win_count = win_count + 1;
        end
    end

    initial begin
        win_count = 0;
        err_count = 0;
        start = 0; valid = 0; pixel = 0; rst_n = 0;

        // Test image: pixel[i] = i mod 256
        for (n = 0; n < 784; n = n + 1)
            img[n] = n % 256;

        repeat (4) @(posedge clk);
        rst_n = 1;
        repeat (2) @(posedge clk);

        // start pulse, then pixels one clock later
        @(negedge clk); start = 1;
        @(negedge clk); start = 0;

        // Frame 1: continuous valid
        for (n = 0; n < 784; n = n + 1) begin
            @(negedge clk);
            valid = 1; pixel = img[n];
        end
        @(negedge clk); valid = 0;
        repeat (5) @(posedge clk);

        if (win_count !== 676) begin
            $display("FAIL frame1: window count = %0d (expected 676)", win_count);
            err_count = err_count + 1;
        end

        // Frame 2: valid with random gaps (counters must advance only on valid)
        win_count = 0;
        @(negedge clk); start = 1;
        @(negedge clk); start = 0;

        for (n = 0; n < 784; n = n + 1) begin
            while ($random % 3 == 0) begin
                @(negedge clk); valid = 0;
            end
            @(negedge clk);
            valid = 1; pixel = img[n];
        end
        @(negedge clk); valid = 0;
        repeat (5) @(posedge clk);

        if (win_count !== 676) begin
            $display("FAIL frame2: window count = %0d (expected 676)", win_count);
            err_count = err_count + 1;
        end

        if (err_count == 0) $display("PASS: all 676 windows correct, 2 frames");
        else                $display("FAIL: %0d errors", err_count);
        $finish;
    end

endmodule

`default_nettype wire