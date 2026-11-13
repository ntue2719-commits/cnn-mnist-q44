`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/23/2026 10:24:24 PM
// Design Name: Nguyen Tri Tue
// Module Name: feature_mem2
//
// Description:
//   Phase-1 feature memory for Pool2 canonical output.
//
// Capacity:
//   25 x 64-bit
//   = 200 INT8 values
//
// Write behavior:
//   - i_start resets write pointer to address 0.
//   - if i_start and i_wr_valid are high together,
//     i_wr_data is written to address 0 on that same cycle.
//   - writes are accepted only for addresses 0..24.
//   - writes after address 24 are ignored.
//   - no wraparound.
//
// Read behavior:
//   - i_start_rd starts sequential read.
//   - start cycle itself does not produce valid data.
//   - next cycle produces mem[0] with o_rd_valid=1.
//   - exactly 25 valid read cycles are produced.
//   - after mem[24], reading stops.
//   - no wraparound.
//
// Reset:
//   active-low synchronous reset.
//   RAM contents are not cleared.
//////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module feature_mem2 (
    input  wire        i_clk,
    input  wire        i_rst_n,

    input  wire        i_start,

    input  wire        i_wr_valid,
    input  wire [63:0] i_wr_data,

    input  wire        i_start_rd,

    output reg         o_rd_valid,
    output reg  [63:0] o_rd_data
);

    // =========================================================================
    // Memory
    // =========================================================================

    reg [63:0] mem [0:24];

    // =========================================================================
    // Write control
    // =========================================================================

    reg [4:0] wr_addr;
    reg       wr_full;

    // =========================================================================
    // Read control
    // =========================================================================

    reg [4:0] rd_addr;
    reg       rd_active;

    // =========================================================================
    // Sequential logic
    // =========================================================================

    always @(posedge i_clk) begin

        if (!i_rst_n) begin

            wr_addr <= 5'd0;
            wr_full <= 1'b0;

            rd_addr   <= 5'd0;
            rd_active <= 1'b0;

            o_rd_valid <= 1'b0;
            o_rd_data  <= 64'd0;

        end
        else begin

            // -----------------------------------------------------------------
            // Default read-valid
            // -----------------------------------------------------------------

            o_rd_valid <= 1'b0;

            // =================================================================
            // Write side
            // =================================================================

            if (i_start) begin

                wr_addr <= 5'd0;
                wr_full <= 1'b0;

                // -------------------------------------------------------------
                // Support i_start + i_wr_valid on same cycle.
                // -------------------------------------------------------------

                if (i_wr_valid) begin

                    mem[0] <= i_wr_data;

                    wr_addr <= 5'd1;

                end

            end
            else if (i_wr_valid && !wr_full) begin

                mem[wr_addr] <= i_wr_data;

                if (wr_addr == 5'd24) begin

                    wr_addr <= 5'd24;
                    wr_full <= 1'b1;

                end
                else begin

                    wr_addr <= wr_addr + 5'd1;

                end

            end

            // =================================================================
            // Read side
            // =================================================================

            if (i_start_rd) begin

                // Start/setup cycle.
                rd_addr   <= 5'd0;
                rd_active <= 1'b1;

            end
            else if (rd_active) begin

                o_rd_valid <= 1'b1;
                o_rd_data  <= mem[rd_addr];

                if (rd_addr == 5'd24) begin

                    rd_addr   <= 5'd0;
                    rd_active <= 1'b0;

                end
                else begin

                    rd_addr <= rd_addr + 5'd1;

                end

            end

        end

    end

endmodule

`default_nettype wire
