# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles


@cocotb.test()
async def test_counter_wraps(dut):
    """The demo boot ROM increments a counter, masks it to 4 bits, and
    stores it to the memory-mapped LED register. This checks that uo_out
    (LED0..7) counts 0..15 and wraps back to 0, matching the standalone
    Icarus testbench used during development (test/tb_top.v in the
    original project skeleton).

    Each instruction takes 7 clock cycles in this core's FSM (up from 5
    -- the FETCH_WAIT/MEM_WAIT states added to support external QSPI
    memory cost 2 extra cycles even for purely on-chip accesses), and
    the demo loop is 4 instructions (addi/andi/sw/jal) after the initial
    addi, so one counter increment = 28 clock cycles.
    """
    dut._log.info("Start")

    clock = Clock(dut.clk, 10, unit="us")  # 100 kHz sim clock
    cocotb.start_soon(clock.start())

    dut._log.info("Reset")
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    dut._log.info("Watch uo_out for at least one full 0..15 wrap")

    last = int(dut.uo_out.value)
    seen_values = {last}
    wrapped = False

    # 28 cycles/increment * 20 increments gives >1 full wrap with margin.
    for _ in range(28 * 20):
        await ClockCycles(dut.clk, 1)
        cur = int(dut.uo_out.value)
        if cur != last:
            assert 0 <= cur <= 15, f"uo_out left expected 0..15 range: {cur}"
            if cur < last:
                wrapped = True
            seen_values.add(cur)
            last = cur

    assert wrapped, "counter never wrapped from 15 back to 0 in the simulated window"
    assert seen_values == set(range(16)), (
        f"expected to see all values 0..15, saw: {sorted(seen_values)}"
    )
