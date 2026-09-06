<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

This is a minimal, from-scratch 32-bit RISC-V (RV32I) CPU, inspired by
[Pineapple ONE](https://pineapple-one.github.io/) -- a RISC-V computer
originally built entirely out of discrete 7400-series logic chips
(no FPGA, no microcontroller). This project keeps that "just basic logic"
spirit but reimplements the CPU as synthesizable Verilog sized to fit a
single Tiny Tapeout tile.

It implements the full RV32I base integer instruction set (LUI, AUIPC,
JAL, JALR, all branches, all loads/stores, and all register-register /
register-immediate ALU operations). FENCE/ECALL/EBREAK decode but act
as no-ops -- there's no trap/exception handling in this version.

The core uses a multi-cycle FSM (fetch -> fetch_wait -> decode ->
execute -> memory -> mem_wait -> writeback), so every instruction takes
7 clock cycles. The two `*_wait` states exist so the core can stall on
a `ready` handshake during a multi-cycle external memory access (see
below) -- for on-chip accesses `ready` is already high the instant the
state is entered, so they cost exactly the same 2 extra cycles as any
other instruction. This keeps the design small and easy to reason
about, at the cost of speed -- which is fitting, since the original
Pineapple ONE ran at 500 kHz too.

Because a Tiny Tapeout tile is far too small for the original design's
512 kB program memory + 512 kB RAM + VGA card, this version uses a much
smaller 256-byte address space:

- `0x00-0x7F`: 128-byte **ROM**, holding the boot program. This is
  implemented as pure combinational logic (a big case statement), not
  flip-flops -- flip-flops don't reliably power up to a known value on
  real silicon, but combinational logic becomes fixed gates at synthesis
  time, so the boot program is guaranteed to be there on every power-on.
- `0x80-0xAF`: 48-byte flip-flop-backed **RAM** for stack/scratch data
  (contents are undefined until your program writes to them).
- `0xB0-0xEF`: 64-byte **external RAM window**, backed by PSRAM ("RAM A")
  on the [Tiny Tapeout QSPI Pmod](https://github.com/mole99/qspi-pmod)
  via a single-line SPI engine (`src/qspi_shared_engine.v`). Reads and
  writes here take many clock cycles (the core stalls on the `ready`
  handshake until the SPI transaction completes) instead of the
  single-cycle response everywhere else on this bus. **Requires the
  QSPI Pmod to be physically attached** -- without it, accesses here
  have undefined results (a floating MISO line), not a defined no-op.
- `0xF0`: memory-mapped **LED output register**, wired to `uo_out`.
- `0xF4`: memory-mapped **switch input register**, wired to `ui_in`.

The default boot program is a simple demo: it increments a counter,
masks it to 4 bits, and stores it to the LED register in a loop --
so `uo_out` counts 0 through 15 on repeat. It does not touch the
external RAM window.

## How to test

Power up the chip (or run the testbench) and watch `uo_out[3:0]`
(LED0-LED3) count from 0 to 15 and wrap back to 0, incrementing roughly
every 28 clock cycles (4 instructions/loop x 7 cycles/instruction).
`uo_out[7:4]` stay at 0 since the counter is masked to 4 bits. `ui_in`
(the switches) isn't read by the default program, but is wired up and
available for your own programs via the memory-mapped register at
address `0xF4`.

To load a different program: hand-encode your RV32I instructions and
replace the `rom_byte` case statement in `src/mem.v`. There is no
assembler included in this project -- either hand-encode short programs
(as the default demo was) or use a real RISC-V toolchain (e.g.
`riscv64-unknown-elf-as`/`objcopy`, targeting plain `rv32i` with no `C`
or `M` extensions, since this core implements neither) and extract the
raw bytes.

Four test suites cover different parts of this design (see `test/`):
`test.py` (cocotb) drives the real top-level module and checks the demo
counter behavior end-to-end; `tb_qspi_engine.v` checks the QSPI engine's
SPI bit-level protocol and byte ordering in isolation; `tb_mem_ext.v`
exercises the external RAM window by driving mem.v's bus signals
directly (as the core's FSM would) through the real engine plus a
behavioral SPI RAM model (`spi_ram_model.v`); and `tb_core_ext.v` closes
the remaining gap by running the *real* `rv32i_core` executing actual
RV32I load/store instructions (`mem_test_ext.v` swaps in a small test
program in place of the production demo) against that same external
window, confirming the address decode, wait-state handshake, QSPI
engine, and byte ordering all work together when driven by the CPU
itself rather than a testbench standing in for it.

**Known limitation:** the external RAM window has only been validated
against the behavioral model in `spi_ram_model.v`, not a real
flash/PSRAM chip or a vendor-accurate behavioral model. Treat the
protocol-level correctness as tested, but not yet hardware-proven.

## External hardware

`uo_out[0:7]` can be connected to LEDs (with series resistors) to watch
the counter directly; `ui_in[0:7]` can be connected to switches/DIP
switches once a program that reads them is loaded.

`uio[0:7]` are wired to the
[Tiny Tapeout QSPI Pmod](https://github.com/mole99/qspi-pmod) pinout:
`uio[0]`=CS0 (flash, reserved/unused this round), `uio[1]`=SD0/MOSI,
`uio[2]`=SD1/MISO, `uio[3]`=SCK, `uio[4:5]`=SD2/SD3 (held high, unused
in single-line mode), `uio[6]`=CS1 (PSRAM "RAM A", backs the
`0xB0-0xEF` window above), `uio[7]`=unused. The QSPI Pmod must be
physically attached for `0xB0-0xEF` accesses to behave -- everything
else on this chip works standalone with no external hardware at all.
