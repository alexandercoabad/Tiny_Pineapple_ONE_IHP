![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# Pineapple ONE (Tiny) — a from-scratch RV32I CPU for Tiny Tapeout (IHP shuttle)

Inspired by [Pineapple ONE](https://pineapple-one.github.io/), a 32-bit
RISC-V CPU built entirely out of discrete 7400-series logic chips (no
FPGA, no microcontroller). This project reimplements that "just basic
logic" spirit as a minimal, from-scratch RV32I core in synthesizable
Verilog, sized to fit a single Tiny Tapeout tile on the IHP `sg13g2`
shuttle, with an optional external QSPI memory expansion.

- [Read the project datasheet](docs/info.md) — how it works, how to test it, pinout
- [Original Pineapple ONE project](https://pineapple-one.github.io/)

**Scope note:** the original design has a 500 kHz clock, 512 kB program
memory, 512 kB RAM, and a VGA card — none of which fits in a TT tile
(~167×108 µm). This project keeps the RV32I instruction set and the
"no FPGA, just logic" philosophy, starting from a 256-byte address
space with no video output, plus a small external RAM window over the
[Tiny Tapeout QSPI Pmod](https://github.com/mole99/qspi-pmod) for
anyone who wants more headroom than the on-chip memory alone gives.

## Layout

<img width="672" height="290" alt="Screenshot 2026-09-05 at 7 54 38 AM" src="https://github.com/user-attachments/assets/27507c0d-4309-4449-8588-f96f205cec55" />



## Status

- [x] Full RV32I base integer ISA (all loads/stores/branches/ALU ops;
      FENCE/ECALL/EBREAK decode as no-ops, no trap support yet)
- [x] Multi-cycle FSM core (fetch/fetch_wait/decode/exec/mem/mem_wait/
      writeback, 7 clock cycles per instruction -- the two `*_wait`
      states let the core stall on a `ready` handshake during external
      QSPI accesses; on-chip accesses see `ready` high immediately)
- [x] Memory: 128 B combinational ROM (boot program) + 48 B flip-flop
      RAM + 64 B external RAM window over the QSPI Pmod + memory-mapped
      LED output (`0xF0`) / switch input (`0xF4`)
- [x] **Hardened successfully on the real `ttihp26b` shuttle CI** at
      6x2 tiles, 59.3% utilization, clean DRC/precheck/gl_test (see
      `.github/workflows/gds.yaml` run history)
- [x] Four passing test suites (see "Testing locally" below): on-chip
      cocotb regression, QSPI engine bit-level protocol, external-window
      integration via direct bus driving, and full CPU-driven external
      load/store — all wired into CI, all gating the build
- [ ] Validate the external memory path against a real flash/PSRAM chip
      or a vendor-accurate behavioral model (currently only tested
      against a hand-written behavioral model, `test/spi_ram_model.v`)
- [ ] Widen the address bus beyond 8 bits to actually reach the QSPI
      Pmod's real multi-megabyte capacity (current external window is
      a fixed 64 bytes within the existing 256-byte address space)

## Repo layout

```
src/
  rv32i_defs.vh          opcode/state constants
  rv32i_core.v            the CPU: regfile, ALU, decode, control FSM
  mem.v                   ROM + RAM + external QSPI window + LED/switch registers
  qspi_shared_engine.v    single-line SPI master shared between flash (CS0) and PSRAM (CS1)
  tt_um_pineapple_one.v   Tiny Tapeout top-level pin mapping (incl. QSPI Pmod pins on uio)
  config.json             LibreLane flow config (clock period, density, etc.)
test/
  tb.v, test.py           cocotb testbench: checks uo_out counts 0..15 and wraps (on-chip only)
  tb_qspi_engine.v        standalone: QSPI engine bit-level protocol + byte-order check
  spi_ram_model.v         behavioral single-line SPI RAM model, for the two tests below
  tb_mem_ext.v            standalone: external window via direct bus driving + real engine + spi_ram_model
  mem_extmem_test.v       copy of mem.v with a test program in place of the production demo
  tb_core_ext.v           standalone: the real CPU running that test program against the external window
info.yaml                 Tiny Tapeout project metadata (title, pinout, tiles...)
docs/info.md              project datasheet shown on the Tiny Tapeout site
```

## How the demo program works

The default boot ROM runs a small loop: increment a counter, mask it to
4 bits, store it to the LED register. So `uo_out[3:0]` counts 0 → 15 on
repeat, `uo_out[7:4]` stay at 0. It does not touch the external RAM
window. Full details, pinout, and how to load your own program are in
[docs/info.md](docs/info.md).

## Before you submit — TODOs left in this repo

1. **Fill in `info.yaml`**: `author` and `discord` are still placeholders.
2. **Validate the external memory path against real hardware.** Everything
   in `test/` passes in simulation, including against the shuttle's own
   gate-level netlist, but the QSPI engine has only been checked against
   a hand-written behavioral model (`test/spi_ram_model.v`), not a real
   flash/PSRAM chip or vendor-accurate model. Treat that path as tested
   groundwork, not hardware-proven, until you've run it against an
   actual QSPI Pmod.
3. **Gate-level sim**, if you haven't already: once CI produces
   `gate_level_netlist.v`, run `make GATES=yes` in `test/` to confirm
   the design still behaves correctly post-synthesis.

Note: Tiny Tapeout requires unique top-module names across a shuttle.
`tt_um_pineapple_one` is a fine name to keep, but it's a somewhat
guessable/popular one — if the submission form flags a collision with
another project on the `ttihp26b` shuttle, that's the one thing you'll
need to adjust (`info.yaml`, `src/tt_um_pineapple_one.v`, `test/tb.v`).

## Testing locally

```
cd test
pip install -r requirements.txt
make                    # cocotb: on-chip regression (counter demo)
make standalone-tests   # QSPI engine + external-window + full-CPU tests
```

Both targets are also run automatically by `.github/workflows/test.yaml`
on every push, and both must pass for that workflow to go green.

## External memory over QSPI

`uio[0:7]` are wired to the
[Tiny Tapeout QSPI Pmod](https://github.com/mole99/qspi-pmod): a
single-line SPI master (`src/qspi_shared_engine.v`) shared between an
external flash chip (CS0, reserved/unused this round) and PSRAM (CS1),
backing a 64-byte external RAM window at `0xB0-0xEF` in `mem.v`. The
core's FSM stalls on a `ready` handshake while an external access is in
flight, and picks up exactly where it left off once the SPI transaction
completes — on-chip accesses are unaffected and still get an immediate
response. See `docs/info.md` for the full address map and pinout.

This currently only uses a fixed 64 bytes of the Pmod's actual
multi-megabyte capacity, since the CPU's address bus is still 8 bits
wide. Reaching the Pmod's real capacity means widening `pc`/`mem_addr`
and the jump/branch immediate math throughout `rv32i_core.v` — a bigger
follow-up change, not yet done here.

## What is Tiny Tapeout?

Tiny Tapeout is an educational project that aims to make it easier and cheaper than ever to get your digital and analog designs manufactured on a real chip.

To learn more and get started, visit https://tinytapeout.com.

## Resources

- [FAQ](https://tinytapeout.com/faq/)
- [Digital design lessons](https://tinytapeout.com/digital_design/)
- [Learn how semiconductors work](https://tinytapeout.com/siliwiz/)
- [Join the community](https://tinytapeout.com/discord)
- [Build your design locally](https://www.tinytapeout.com/guides/local-hardening/)

## What next?

- [Submit your design to the next shuttle](https://app.tinytapeout.com/).
- Share your project on your social network of choice:
  - LinkedIn [#tinytapeout](https://www.linkedin.com/search/results/content/?keywords=%23tinytapeout) [@TinyTapeout](https://www.linkedin.com/company/100708654/)
  - Mastodon [#tinytapeout](https://chaos.social/tags/tinytapeout) [@matthewvenn](https://chaos.social/@matthewvenn)
  - X (formerly Twitter) [#tinytapeout](https://twitter.com/hashtag/tinytapeout) [@tinytapeout](https://twitter.com/tinytapeout)
  - Bluesky [@tinytapeout.com](https://bsky.app/profile/tinytapeout.com)
