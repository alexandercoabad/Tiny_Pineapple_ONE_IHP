![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# Pineapple ONE (Tiny) — a from-scratch RV32I CPU for Tiny Tapeout (IHP shuttle)

Inspired by [Pineapple ONE](https://pineapple-one.github.io/), a 32-bit
RISC-V CPU built entirely out of discrete 7400-series logic chips (no
FPGA, no microcontroller). This project reimplements that "just basic
logic" spirit as a minimal, from-scratch RV32I core in synthesizable
Verilog, sized to fit a single Tiny Tapeout tile on the IHP `sg13g2`
shuttle.

- [Read the project datasheet](docs/info.md) — how it works, how to test it, pinout
- [Original Pineapple ONE project](https://pineapple-one.github.io/)

**Scope note:** the original design has a 500 kHz clock, 512 kB program
memory, 512 kB RAM, and a VGA card — none of which fits in a TT tile
(~167×108 µm). This project keeps the RV32I instruction set and the
"no FPGA, just logic" philosophy, but starts from a 256-byte address
space with no video output. See "Roadmap" below for the planned path
to real external memory.

## Layout

<img width="672" height="290" alt="Screenshot 2026-09-05 at 7 54 38 AM" src="https://github.com/user-attachments/assets/27507c0d-4309-4449-8588-f96f205cec55" />



## Status

- [x] Full RV32I base integer ISA (all loads/stores/branches/ALU ops;
      FENCE/ECALL/EBREAK decode as no-ops, no trap support yet)
- [x] 5-stage multi-cycle FSM core (fetch/decode/exec/mem/writeback,
      5 clock cycles per instruction)
- [x] Memory: 128 B combinational ROM (boot program) + 112 B flip-flop
      RAM + memory-mapped LED output (`0xF0`) / switch input (`0xF4`)
- [x] Passing cocotb testbench (`test/test.py`, run via `make` in `test/`)
- [ ] Run through LibreLane on the actual `ttihp26b` shuttle CI to get
      real area/timing numbers (not yet pushed/run — see "Next steps")
- [ ] Gate-level simulation to confirm the ROM synthesizes as pure
      combinational logic rather than something unexpected
- [ ] External SPI memory for a larger address space (milestone 2)

## Repo layout

```
src/
  rv32i_defs.vh          opcode/state constants
  rv32i_core.v            the CPU: regfile, ALU, decode, control FSM
  mem.v                   ROM + RAM + memory-mapped LED/switch registers
  tt_um_pineapple_one.v   Tiny Tapeout top-level pin mapping
  config.json             LibreLane flow config (clock period, density, etc.)
test/
  tb.v, test.py           cocotb testbench: checks uo_out counts 0..15 and wraps
info.yaml                 Tiny Tapeout project metadata (title, pinout, tiles...)
docs/info.md              project datasheet shown on the Tiny Tapeout site
```

## How the demo program works

The default boot ROM runs a small loop: increment a counter, mask it to
4 bits, store it to the LED register. So `uo_out[3:0]` counts 0 → 15 on
repeat, `uo_out[7:4]` stay at 0. Full details, pinout, and how to load
your own program are in [docs/info.md](docs/info.md).

## Before you submit — TODOs left in this repo

1. **Fill in `info.yaml`**: `author` and `discord` are still placeholders.
2. **Run the real flow.** This has only been verified in RTL simulation
   (Icarus + cocotb) so far — push to GitHub and let the `gds` workflow
   run LibreLane against IHP `sg13g2` to get real area and timing
   numbers before relying on any of this. If it doesn't fit or times
   out at 1 MHz, the ALU's shift/comparison logic is the first place to
   trim, or increase `CLOCK_PERIOD` in `src/config.json` further.
3. **Gate-level sim.** Once CI produces `gate_level_netlist.v`, run
   `make GATES=yes` in `test/` to confirm the design still behaves
   correctly post-synthesis — this is what actually verifies the ROM
   survived as combinational logic instead of relying on flip-flop
   initial state (which real silicon won't honor).

Note: Tiny Tapeout requires unique top-module names across a shuttle.
`tt_um_pineapple_one` is a fine name to keep, but it's a somewhat
guessable/popular one — if the submission form flags a collision with
another project on the `ttihp26b` shuttle, that's the one thing you'll
need to adjust (`info.yaml`, `src/tt_um_pineapple_one.v`, `test/tb.v`).

## Testing locally

```
cd test
pip install -r requirements.txt
make
```

This builds and runs the cocotb testbench with Icarus Verilog, exactly
as the CI does for RTL simulation.

## Roadmap: external memory over SPI (milestone 2)

Tiny Tapeout's demo board can emulate a large external RAM over SPI
from its onboard RP2040, using CS/MOSI/MISO/SCK on `uio[0:3]` — the
`uio` pins are currently unused and reserved for exactly this. Growing
past 256 bytes means replacing `mem.v`'s combinational read with an SPI
master plus a `mem_ready`/stall signal in the core's FSM between the
`EXEC` and `MEM` states; the register file, ALU, and decode logic don't
need to change.

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
