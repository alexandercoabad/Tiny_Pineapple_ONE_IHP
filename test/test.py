# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer


# uio pin mapping (see src/tt_um_pineapple_one.v):
#   uio[0]=qspi_cs0  uio[1]=qspi_mosi  uio[2]=qspi_miso (input to chip)
#   uio[3]=qspi_sck  uio[6]=qspi_cs1   (used by the external RAM window)
UIO_CS0, UIO_MOSI, UIO_MISO, UIO_SCK, UIO_CS1 = 0, 1, 2, 3, 6

# ui_in pin mapping for the GPIO bootloader protocol (see docs/info.md
# and tools/build_boot_rom.py):
#   ui_in[0]=DATA  ui_in[1]=CLOCK  ui_in[2]=START
UI_DATA, UI_CLOCK, UI_START = 0, 1, 2

CMD_READ = 0x03
CMD_WRITE = 0x02
DEBUG = False


async def qspi_ram_slave(dut, corrupt_reads=False, log=None):
    """Behavioral single-line SPI RAM slave, driven by sampling
    dut.uio_out every system clock edge and comparing against the
    previous sample to detect sck/cs edges -- mirrors
    test/spi_ram_model.v's protocol (same CMD_READ/CMD_WRITE, 24-bit
    address, MSB-first cmd+addr, byte-at-a-time data), just implemented
    in Python instead of Verilog so it can run against the gate-level
    netlist via cocotb. Runs forever as a background task; persists
    written bytes for the lifetime of one test.

    corrupt_reads: if True, every read returns the bitwise complement
    of the stored byte instead of the real value -- used to prove the
    self-test's mismatch detection actually inspects the data.

    log: optional list. If provided, a dict is appended to it each
    time an address phase completes: {"we": bool, "addr": int}.
    """
    mem = bytearray(256)
    prev_sck = 0
    bitcnt = 0
    phase = 0  # 0=cmd, 1=addr, 2=data
    addr = 0
    addr_byte = 0
    we = False
    shift_in = 0
    cur_out_bit = 0

    while True:
        await RisingEdge(dut.clk)
        uio = int(dut.uio_out.value)
        cs1 = (uio >> UIO_CS1) & 1
        sck = (uio >> UIO_SCK) & 1
        mosi = (uio >> UIO_MOSI) & 1

        if cs1 == 1:
            phase = 0
            bitcnt = 0
            addr_byte = 0
            dut.uio_in.value = int(dut.uio_in.value) & ~(1 << UIO_MISO)
        elif prev_sck == 0 and sck == 1:
            shift_in = ((shift_in << 1) | mosi) & 0xFF
            bitcnt += 1
            if bitcnt == 8:
                bitcnt = 0
                if phase == 0:
                    we = (shift_in == CMD_WRITE)
                    phase = 1
                    if DEBUG: print(f"  [slave] CMD byte = 0x{shift_in:02x} we={we}")
                elif phase == 1:
                    addr = ((addr << 8) | shift_in) & 0xFFFFFF
                    addr_byte += 1
                    if addr_byte == 3:
                        phase = 2
                        raw = mem[addr & 0xFF]
                        cur_out_bit = (~raw) & 0xFF if corrupt_reads else raw
                        if log is not None:
                            log.append({"we": we, "addr": addr & 0xFF})
                else:  # phase == 2, data
                    if we:
                        mem[addr & 0xFF] = shift_in
                        if DEBUG: print(f"  [slave] WROTE mem[0x{addr&0xFF:02x}] = 0x{shift_in:02x}")
                    addr = (addr + 1) & 0xFFFFFF
                    raw = mem[addr & 0xFF]
                    cur_out_bit = (~raw) & 0xFF if corrupt_reads else raw
        elif prev_sck == 1 and sck == 0 and phase == 2 and not we:
            bit_idx = 7 - bitcnt
            bitval = (cur_out_bit >> bit_idx) & 1
            cur = int(dut.uio_in.value)
            dut.uio_in.value = (cur & ~(1 << UIO_MISO)) | (bitval << UIO_MISO)
            await Timer(1, units="ns")

        prev_sck = sck


async def reset_dut(dut):
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1


async def wait_for_first_led_write(dut, max_cycles=500):
    """Waits for the first nonzero write to uo_out -- the end of the
    self-test prefix and the start of the demo/listen loop."""
    for _ in range(max_cycles):
        await ClockCycles(dut.clk, 1)
        if int(dut.uo_out.value) != 0:
            return
    assert False, "counter never wrote anything -- self-test/boot prefix may be stuck"


# ---------------------------------------------------------------------
# GPIO bootloader helpers: DATA/CLOCK/START on ui_in[0:2] (see
# docs/info.md and tools/build_boot_rom.py's module docstring for the
# full protocol). Hold each phase generously long (the design's own
# RECV_BYTE polls GPIO_IN in a ~3-instruction/21-cycle software loop;
# a short-lived pulse can be missed by that poll, and -- worse -- a
# fixed pace only slightly faster than the poll loop lets per-bit lag
# accumulate over a long transfer until pulses are dropped outright.
# 200 cycles/phase leaves a wide safety margin either way).
# ---------------------------------------------------------------------
BOOT_BIT_HOLD_CYCLES = 200


async def boot_send_bit(dut, bit):
    dut.ui_in.value = (int(dut.ui_in.value) & ~((1 << UI_DATA) | (1 << UI_CLOCK))) | (bit << UI_DATA)
    await ClockCycles(dut.clk, BOOT_BIT_HOLD_CYCLES)
    dut.ui_in.value = int(dut.ui_in.value) | (1 << UI_CLOCK)
    await ClockCycles(dut.clk, BOOT_BIT_HOLD_CYCLES)
    dut.ui_in.value = int(dut.ui_in.value) & ~(1 << UI_CLOCK)
    await ClockCycles(dut.clk, BOOT_BIT_HOLD_CYCLES)


async def boot_send_byte(dut, byte):
    for i in range(7, -1, -1):
        await boot_send_bit(dut, (byte >> i) & 1)


async def boot_send_program(dut, program_bytes):
    """Asserts START, then streams a length-prefixed program over the
    DATA/CLOCK handshake. Caller is responsible for having already
    reset the DUT and waited past the self-test."""
    dut.ui_in.value = int(dut.ui_in.value) | (1 << UI_START)
    await ClockCycles(dut.clk, 20)
    await boot_send_byte(dut, len(program_bytes))
    for b in program_bytes:
        await boot_send_byte(dut, b)


@cocotb.test()
async def test_counter_wraps(dut):
    """The boot ROM self-tests the external QSPI PSRAM at power-on
    (see test_selftest_fails_without_pmod / test_selftest_passes_with_pmod),
    then enters a loop that increments a 4-bit demo counter into
    uo_out[3:0] (OR'd with the self-test flag in uo_out[7]) while
    polling ui_in[2] (START) every iteration, indefinitely -- unlike
    the original single-shot demo, this loop never exits on its own;
    it's always listening for a bootload request. This checks
    uo_out[3:0] counts 0..15 and wraps, with ui_in held at 0 (no
    bootload requested) so the loop just keeps blinking.
    """
    dut._log.info("Start")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)

    last = int(dut.uo_out.value) & 0x0F
    seen_values = {last}
    wrapped = False

    # Self-test prefix + several loop iterations (each iteration is
    # longer now that it also polls GPIO_IN for START: 8
    # instructions/iteration x 7 cycles/instruction = 56 cycles).
    for _ in range(500 + 56 * 20):
        await ClockCycles(dut.clk, 1)
        cur = int(dut.uo_out.value) & 0x0F
        if cur != last:
            assert 0 <= cur <= 15, f"uo_out[3:0] left expected 0..15 range: {cur}"
            if cur < last:
                wrapped = True
            seen_values.add(cur)
            last = cur

    assert wrapped, "counter never wrapped from 15 back to 0 in the simulated window"
    assert seen_values == set(range(16)), (
        f"expected to see all values 0..15, saw: {sorted(seen_values)}"
    )


@cocotb.test()
async def test_reset_starts_from_zero(dut):
    """Immediately after reset, uo_out should read 0 -- mem.v resets
    gpio_out to 0 directly, and the self-test prefix never touches the
    LED register (it only writes to the external RAM window).
    """
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)

    await ClockCycles(dut.clk, 5)
    assert int(dut.uo_out.value) == 0, (
        f"expected uo_out == 0 immediately after reset, got {int(dut.uo_out.value)}"
    )


@cocotb.test()
async def test_ui_in_upper_bits_do_not_affect_counter(dut):
    """ui_in[2:0] are reserved for the GPIO bootloader protocol
    (START/CLOCK/DATA) and are read every loop iteration by design --
    unlike the original no-bootloader demo, this program DOES read
    ui_in now, deliberately. ui_in[7:3] remain unused by the boot ROM
    (available for whatever gets bootloaded), so this drives a
    changing pattern across just those bits -- never touching
    START/CLOCK/DATA -- and confirms the counter still counts 0..15
    and wraps exactly as it does with ui_in held at 0.
    """
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    last = int(dut.uo_out.value) & 0x0F
    seen_values = {last}
    wrapped = False

    for i in range(500 + 56 * 20):
        dut.ui_in.value = (i & 0x1F) << 3  # only touch bits [7:3]
        await ClockCycles(dut.clk, 1)
        cur = int(dut.uo_out.value) & 0x0F
        if cur != last:
            assert 0 <= cur <= 15, f"uo_out[3:0] left expected 0..15 range: {cur}"
            if cur < last:
                wrapped = True
            seen_values.add(cur)
            last = cur

    assert wrapped, "counter never wrapped with ui_in[7:3] toggling"
    assert seen_values == set(range(16)), (
        f"expected to see all values 0..15 with ui_in[7:3] toggling, saw: {sorted(seen_values)}"
    )


@cocotb.test()
async def test_selftest_fails_without_pmod(dut):
    """With nothing driving MISO (no Pmod attached), the boot ROM's
    self-test should safely detect the mismatch and set uo_out[7],
    while the demo counter on uo_out[3:0] keeps running normally.
    """
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)
    await wait_for_first_led_write(dut)

    assert (int(dut.uo_out.value) >> 7) & 1 == 1, (
        "expected uo_out[7]=1 (self-test failed) with no QSPI slave attached"
    )

    seen_values = set()
    for _ in range(56 * 20):
        await ClockCycles(dut.clk, 1)
        val = int(dut.uo_out.value)
        assert (val >> 7) & 1 == 1, "uo_out[7] should stay 1 once set"
        seen_values.add(val & 0x0F)
    assert seen_values == set(range(16)), (
        f"expected counter to still visit all 0..15, saw: {sorted(seen_values)}"
    )


@cocotb.test()
async def test_selftest_passes_with_pmod(dut):
    """With a QSPI RAM slave actually responding on the Pmod pins, the
    self-test should genuinely pass: write 0xA5 to the external
    window, read it back, match, and clear uo_out[7].
    """
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)
    cocotb.start_soon(qspi_ram_slave(dut))
    await wait_for_first_led_write(dut)

    assert (int(dut.uo_out.value) >> 7) & 1 == 0, (
        "expected uo_out[7]=0 (self-test passed) with a QSPI RAM slave attached"
    )


@cocotb.test()
async def test_selftest_detects_mismatch(dut):
    """A slave that writes fine but returns corrupted data on read
    should make the self-test correctly report failure -- proving the
    self-test genuinely compares the read-back value rather than just
    checking that some response arrived.
    """
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)
    cocotb.start_soon(qspi_ram_slave(dut, corrupt_reads=True))
    await wait_for_first_led_write(dut)

    assert (int(dut.uo_out.value) >> 7) & 1 == 1, (
        "expected uo_out[7]=1 (self-test failed) when the QSPI slave echoes back corrupted data"
    )


@cocotb.test()
async def test_selftest_transaction_addresses_match(dut):
    """The self-test is a write-then-read-back of the SAME external
    address. Logs every completed address phase and checks: the first
    transaction is a write, the second is a read, and both target the
    same byte address.
    """
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)
    txns = []
    cocotb.start_soon(qspi_ram_slave(dut, log=txns))
    await wait_for_first_led_write(dut)

    assert len(txns) >= 2, f"expected at least 2 QSPI transactions before the loop starts, saw {txns}"
    write_txn, read_txn = txns[0], txns[1]
    assert write_txn["we"] is True, f"expected the first self-test transaction to be a write, got {write_txn}"
    assert read_txn["we"] is False, f"expected the second self-test transaction to be a read, got {read_txn}"
    assert write_txn["addr"] == read_txn["addr"], (
        f"self-test wrote to 0x{write_txn['addr']:02x} but read back from 0x{read_txn['addr']:02x}"
    )


@cocotb.test()
async def test_flash_cs_never_asserted(dut):
    """uio[0] (qspi_cs0, the flash chip-select) should never assert
    while the boot ROM itself is running -- it always targets the
    PSRAM window (CS1) for its self-test. FLASH_MODE (0xF8) only
    matters to a program that's already been bootloaded and chooses to
    set it; the boot ROM never touches it itself.
    """
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)
    cocotb.start_soon(qspi_ram_slave(dut))

    for _ in range(500 + 56 * 5):
        await ClockCycles(dut.clk, 1)
        cs0 = (int(dut.uio_out.value) >> UIO_CS0) & 1
        assert cs0 == 1, "qspi_cs0 (uio[0]) went low during the boot ROM's own execution"


@cocotb.test()
async def test_uio_oe_is_constant(dut):
    """uio_oe should be fixed at 0b1111_1011 (every uio pin driven as
    an output except uio[2]/MISO) for the whole run.
    """
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)
    cocotb.start_soon(qspi_ram_slave(dut))

    for _ in range(500 + 56 * 5):
        await ClockCycles(dut.clk, 1)
        oe = int(dut.uio_oe.value)
        assert oe == 0b1111_1011, f"uio_oe changed to 0b{oe:08b}, expected constant 0b11111011"


@cocotb.test()
async def test_selftest_passes_again_after_soft_reset(dut):
    """A soft reset (rst_n toggled low/high again without power-cycling)
    only resets the chip's internal state -- a real external PSRAM
    chip keeps whatever it had from before. Runs the self-test twice
    against the same long-lived slave instance and checks it passes
    both times.
    """
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    cocotb.start_soon(qspi_ram_slave(dut))

    for attempt in (1, 2):
        await reset_dut(dut)
        await wait_for_first_led_write(dut)
        assert (int(dut.uo_out.value) >> 7) & 1 == 0, (
            f"expected uo_out[7]=0 (self-test passed) on boot attempt {attempt} after a soft reset"
        )


@cocotb.test()
async def test_bootloader_loads_and_runs_program(dut):
    """The headline new capability: bootload a small RV32I program
    over ui_in[0:2] (DATA/CLOCK/START) and confirm it actually runs
    from the loadable RAM window (0xB4-0xDF), not just that bytes were
    received.

    Program: addi x1,x0,5 ; addi x2,x0,7 ; add x3,x1,x2 ; sw x3,0xF0(x0)
    ; jal x0,0 (spin) -- five 4-byte RV32I instructions computing 5+7
    and writing the result to LED_OUT, then looping in place so the
    result is stable to observe. Encoded by hand here (not via
    tools/build_boot_rom.py, which only assembles the boot ROM itself)
    to keep this test's expected program self-contained and readable.
    """
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)
    cocotb.start_soon(qspi_ram_slave(dut))

    # Let the self-test resolve and the demo loop start blinking before
    # requesting a bootload -- exercises the "interrupt the demo, don't
    # just catch it at a fixed post-reset window" listening behavior.
    await ClockCycles(dut.clk, 200)

    program = bytes([
        0x93, 0x00, 0x50, 0x00,  # addi x1, x0, 5
        0x13, 0x01, 0x70, 0x00,  # addi x2, x0, 7
        0xb3, 0x81, 0x20, 0x00,  # add  x3, x1, x2
        0x23, 0x28, 0x30, 0x0e,  # sw   x3, 0xF0(x0)   -- LED_OUT
        0x6f, 0x00, 0x00, 0x00,  # jal  x0, 0          -- spin
    ])
    await boot_send_program(dut, program)

    # Generous settle time: loading + a handful of instruction cycles.
    await ClockCycles(dut.clk, 3000)

    assert int(dut.uo_out.value) & 0x0F == 12, (
        f"expected uo_out[3:0]=12 (5+7) after the bootloaded program ran, "
        f"got uo_out=0b{int(dut.uo_out.value):08b}"
    )
