// mem.v -- byte-addressable memory for Pineapple-TT
//
// Address map (8-bit address space, 256 bytes total):
//   0x00 - 0xAF : BOOT ROM (176 bytes / 44 instructions) -- combinational,
//                   fixed logic at synthesis time, always present with or
//                   without any Pmod/host attached. Holds the self-test +
//                   demo/listen loop + bootloader -- see
//                   tools/build_boot_rom.py and "Reprogrammability" below.
//                   Safe to rely on at power-up on real silicon since it
//                   is NOT flip-flop state.
//   0xB0 - 0xDF : RAM (48 bytes) -- flip-flops, undefined at power-on on
//                   real silicon. Split into two sub-ranges:
//                     0xB0-0xB3 : always on-chip scratch, never affected
//                                 by FLASH_MODE. The boot ROM doesn't use
//                                 these itself; free for whatever gets
//                                 bootloaded (e.g. its own stack).
//                     0xB4-0xDF : the bootloader's load target (44 bytes).
//                                 The CPU's own fetch/data window once a
//                                 program is running there -- same bytes
//                                 serve both roles, no Harvard split
//                                 needed. When FLASH_MODE (0xF8) has been
//                                 set, reads of *this* sub-range are
//                                 transparently sourced from external
//                                 flash (CS0) instead -- see below. Writes
//                                 to it while FLASH_MODE is set are
//                                 dropped (real NOR flash can't be
//                                 written with a plain 0x02 command the
//                                 way PSRAM can).
//   0xE0 - 0xEF : RAM (16 bytes) -- external PSRAM ("RAM A" / CS1) on the
//                   Tiny Tapeout QSPI Pmod, via qspi_shared_engine. Reads/
//                   writes here take multiple clock cycles (the core
//                   stalls on `ready` until the SPI transaction
//                   completes) instead of the single-cycle response
//                   everything else on this bus gets. This is also what
//                   the boot ROM's own power-on self-test probes (write
//                   0xA5, read back, compare) to light uo_out[7] when no
//                   Pmod is attached. Unaffected by FLASH_MODE -- always
//                   PSRAM.
//   0xF0        : LED_OUT   (memory-mapped, write-only, drives uo_out)
//   0xF4        : SW_IN     (memory-mapped, read-only, reflects ui_in --
//                   also the bootloader's DATA/CLOCK/START input)
//   0xF8        : FLASH_MODE (memory-mapped, write-only, write-any-value-
//                   to-set -- see "Reprogrammability" below)
//
// Word accesses (LW/SW) must be 4-byte aligned. Byte/half accesses
// (LB/LH/SB/SH) are supported at any address within a region.
//
// ---------------------------------------------------------------------
// Reprogrammability
// ---------------------------------------------------------------------
// 0x00-0xAF used to hold a fixed demo *application* program, baked in as
// synthesized combinational logic -- permanent the instant the chip was
// taped out. It now holds a fixed boot ROM instead (see
// tools/build_boot_rom.py), following the same pattern AgilA8's
// boot_rom/shared_ram split uses, adapted to this core's single unified
// 8-bit address space (PC and mem_addr are the same bus here, so a
// loaded program is immediately both writable AND fetchable at the same
// address -- no separate IMEM/DMEM aliasing trick needed the way
// AgilA8's shared_ram requires).
//
// On every reset the boot ROM runs first, from 0x00. It self-tests the
// external PSRAM window (write/read/compare, result latched into
// uo_out[7]), then enters an indefinite demo/listen loop -- blinking a
// counter into uo_out[3:0] while polling ui_in[2] (START) every
// iteration, forever, not just in a bounded post-reset window. Once
// START is seen, it bit-bangs a length-prefixed program over ui_in[0:2]
// (DATA/CLOCK/START -- see build_boot_rom.py's docstring for the exact
// wire protocol) into the 0xB4-0xDF RAM window using plain SB
// instructions, then jumps to 0xB4 to run it -- no special hardware
// write port, just software issuing ordinary stores.
//
// The boot ROM itself never touches FLASH_MODE. It's there for a
// bootloaded program to opt into: writing FLASH_MODE (0xF8, write-any-
// value-to-set) makes the *same* 0xB4-0xDF window resolve to external
// flash (CS0) instead of on-chip RAM from then on, so a chip with a
// flashed QSPI Pmod attached can be set up (once, by something you
// bootload) to boot straight from flash on subsequent power-cycles
// without the host re-pushing anything over the wire. Reflashing that
// chip afterward is a normal SPI flash write, not a new tapeout.
//
// This is a single-cycle combinational read / synchronous write memory
// for the on-chip regions; the external windows (0xB4-0xDF in
// FLASH_MODE, and 0xE0-0xEF always) hand off to qspi_shared_engine and
// stall the core on `ready` for as many cycles as the SPI transaction
// needs.

`default_nettype none

module mem #(
    parameter ROM_BYTES      = 176,
    parameter RAM_BASE       = 8'hB0,
    parameter RAM_BYTES      = 48,
    parameter LOAD_BASE      = 8'hB4,   // start of the FLASH_MODE-redirectable sub-range
    parameter EXT_PSRAM_BASE = 8'hE0,
    parameter EXT_PSRAM_BYTES= 16
) (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [7:0]  addr,       // byte address
    input  wire [31:0] wdata,
    input  wire [1:0]  size,       // 0=byte, 1=half, 2=word
    input  wire        we,
    input  wire        valid,      // held high by the core for the whole access
    output wire        ready,      // 1 whenever no external transaction is
                                   // in flight -- on-chip accesses always
                                   // see this high immediately (same 1-cycle
                                   // timing as before this port existed)
    output reg  [31:0] rdata,

    input  wire [7:0]  gpio_in,    // ui_in, mapped at 0xF4 -- also the
                                   // bootloader's bit-bang input
    output reg  [7:0]  gpio_out,   // uo_out, mapped at 0xF0

    // Tiny Tapeout QSPI Pmod pins (single-line mode). CS0/flash backs
    // the FLASH_MODE-redirected 0xB4-0xDF window; CS1/psram backs the
    // 0xE0-0xEF window (and is what the boot ROM's self-test probes).
    output wire        qspi_cs0,   // flash CS
    output wire        qspi_cs1,   // psram CS
    output wire        qspi_sck,
    output wire        qspi_mosi,
    input  wire        qspi_miso
);

    // ---------------------------------------------------------------
    // Boot ROM: fixed self-test + demo/listen loop + bootloader, one
    // byte per line, little-endian. Generated by
    // tools/build_boot_rom.py -- regenerate that and re-copy its
    // output here if the boot ROM routine changes; don't hand-edit
    // the case statement.
    // ---------------------------------------------------------------
    function [7:0] rom_byte;
        input [7:0] a;
        begin
            case (a)
`include "boot_rom_body.vh"
                default: rom_byte = 8'h00; // unused ROM space, never fetched
            endcase
        end
    endfunction

    wire [31:0] rom_word = {rom_byte(addr+8'd3), rom_byte(addr+8'd2), rom_byte(addr+8'd1), rom_byte(addr)};

    // ---------------------------------------------------------------
    // RAM: RAM_BYTES bytes (default 48) at RAM_BASE (default 0xB0),
    // flip-flop backed. 0xB0-0xB3 is plain scratch; LOAD_BASE-and-up
    // (0xB4-0xDF) is the bootloader's load target AND, once running,
    // the CPU's own fetch/data window -- unless FLASH_MODE has
    // redirected reads of that sub-range to external flash (below).
    // ---------------------------------------------------------------
    reg [7:0] ram [0:RAM_BYTES-1];
    integer i;

    // synthesis translate_off
    initial for (i = 0; i < RAM_BYTES; i = i + 1) ram[i] = 8'h00;
    // synthesis translate_on

    // ---------------------------------------------------------------
    // FLASH_MODE: write-any-value-to-set, no readback, sticky until
    // reset. Never touched by the boot ROM itself -- opt-in for
    // whatever gets bootloaded (see header).
    // ---------------------------------------------------------------
    reg flash_mode;

    wire in_rom        = (addr < ROM_BYTES);
    wire in_ram_range  = (addr >= RAM_BASE) && (addr < (RAM_BASE + RAM_BYTES));
    wire in_load_range = (addr >= LOAD_BASE) && (addr < (RAM_BASE + RAM_BYTES));
    wire in_ext_flash  = flash_mode && in_load_range;
    wire in_ram        = in_ram_range && !in_ext_flash;
    wire [7:0] ram_addr = addr - RAM_BASE;

    // ---------------------------------------------------------------
    // External windows via qspi_shared_engine: the LOAD_BASE sub-range
    // when FLASH_MODE is set (CS0/flash), and the PSRAM window
    // (CS1/psram) always. Only one is ever selected for a given
    // access, and the CPU only ever has one access outstanding at a
    // time (mem_valid is never asserted for two different addresses
    // in the same cycle), so a single shared request bus is safe --
    // same reasoning qspi_shared_engine's own header documents.
    // ---------------------------------------------------------------
    wire in_ext_psram = (addr >= EXT_PSRAM_BASE) && (addr < (EXT_PSRAM_BASE + EXT_PSRAM_BYTES));
    wire in_ext       = in_ext_flash || in_ext_psram;

    wire [1:0]  req_dev  = in_ext_flash ? 2'd1 : 2'd2;               // 1=flash(CS0) 2=psram(CS1)
    wire [23:0] ext_addr = in_ext_flash ? {16'h0, (addr - LOAD_BASE)} : {16'h0, (addr - EXT_PSRAM_BASE)};

    // Flash is read-only from this port -- real NOR flash needs an
    // erase/program sequence a plain 0x02 command can't provide, so
    // writes to the FLASH_MODE-redirected sub-range are dropped (see
    // the write path below), and the request to the engine is never
    // issued as a write for that case either.
    wire        ext_req_we    = we && !in_ext_flash;
    wire        ext_req_valid = valid && in_ext;
    wire [31:0] ext_rdata;
    wire        ext_ready;

    // 1 whenever no genuine external transaction is outstanding this
    // cycle -- preserves the original single-cycle response for every
    // on-chip address, and only ever actually waits on ext_ready when
    // this access is both `valid` and inside an external window.
    assign ready = ext_req_valid ? ext_ready : 1'b1;

    qspi_shared_engine u_qspi (
        .clk       (clk),
        .rst_n     (rst_n),
        .req_valid (ext_req_valid),
        .req_we    (ext_req_we),
        .req_dev   (req_dev),
        .req_addr  (ext_addr),
        .req_wdata (wdata),
        .req_size  (size),
        .req_rdata (ext_rdata),
        .req_ready (ext_ready),
        .pin_cs0   (qspi_cs0),
        .pin_cs1   (qspi_cs1),
        .pin_sck   (qspi_sck),
        .pin_mosi  (qspi_mosi),
        .pin_miso  (qspi_miso)
    );

    // ---------------------------------------------------------------
    // Read path
    // ---------------------------------------------------------------
    always @(*) begin
        if (in_rom) begin
            rdata = rom_word;
        end else if (in_ram) begin
            rdata = {ram[ram_addr + 8'd3], ram[ram_addr + 8'd2], ram[ram_addr + 8'd1], ram[ram_addr]};
        end else if (in_ext) begin
            rdata = ext_rdata; // only meaningful once `ready` has pulsed -- see header
        end else if (addr == 8'hF0) begin
            rdata = {24'b0, gpio_out};
        end else if (addr == 8'hF4) begin
            rdata = {24'b0, gpio_in};
        end else begin
            rdata = 32'h0;
        end
    end

    // ---------------------------------------------------------------
    // Write path (synchronous)
    // ---------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            gpio_out   <= 8'h00;
            flash_mode <= 1'b0;
        end else if (we) begin
            if (in_ram_range && !in_ext_flash) begin
                case (size)
                    2'd0: ram[ram_addr] <= wdata[7:0];
                    2'd1: begin
                        ram[ram_addr]       <= wdata[7:0];
                        ram[ram_addr + 8'd1] <= wdata[15:8];
                    end
                    default: begin
                        ram[ram_addr]        <= wdata[7:0];
                        ram[ram_addr + 8'd1] <= wdata[15:8];
                        ram[ram_addr + 8'd2] <= wdata[23:16];
                        ram[ram_addr + 8'd3] <= wdata[31:24];
                    end
                endcase
            end else if (addr == 8'hF0) begin
                gpio_out <= wdata[7:0];
            end else if (addr == 8'hF8) begin
                flash_mode <= 1'b1;   // write-any-value-to-set, sticky until reset
            end
        end
    end

endmodule
