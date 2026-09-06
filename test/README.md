# Sample testbench for a Tiny Tapeout project

This is a sample testbench for a Tiny Tapeout project. It uses [cocotb](https://docs.cocotb.org/en/stable/) to drive the DUT and check the outputs.
See below to get started or for more information, check the [website](https://tinytapeout.com/hdl/testing/).

## Setting up

1. Edit [Makefile](Makefile) and modify `PROJECT_SOURCES` to point to your Verilog files.
2. Edit [tb.v](tb.v) and replace `tt_um_example` with your module name.

## How to run

To run the RTL simulation:

```sh
make -B
```

To run gatelevel simulation, first harden your project and copy `../runs/wokwi/results/final/verilog/gl/{your_module_name}.v` to `gate_level_netlist.v`.

Then run:

```sh
make -B GATES=yes
```

If you wish to save the waveform in VCD format instead of FST format, edit tb.v to use `$dumpfile("tb.vcd");` and then run:

```sh
make -B FST=
```

This will generate `tb.vcd` instead of `tb.fst`.

## Additional standalone testbenches

Two extra testbenches cover the QSPI external-memory addition and are
**not** wired into `make` / the CI `test` workflow (which only runs the
cocotb suite above). Run them directly with iverilog:

```sh
# QSPI engine bit-level protocol check (write bitstream, byte order, CS behavior)
iverilog -g2012 -o /tmp/tb1.vvp ../src/qspi_shared_engine.v tb_qspi_engine.v && vvp /tmp/tb1.vvp

# Full external-window integration test (mem.v + engine + a behavioral SPI RAM model)
iverilog -g2012 -o /tmp/tb2.vvp ../src/mem.v ../src/qspi_shared_engine.v spi_ram_model.v tb_mem_ext.v && vvp /tmp/tb2.vvp
```

Neither of these has been checked against a real flash/PSRAM chip or a
vendor-accurate behavioral model -- see `docs/info.md`'s "Known
limitation" note.

## How to view the waveform file

Using GTKWave

```sh
gtkwave tb.fst tb.gtkw
```

Using Surfer

```sh
surfer tb.fst
```
