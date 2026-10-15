import sys

import pytest

from agent_storage_guard import memory_pressure


def test_parse_real_macos_formats_and_dispatch_flag_semantics():
    sample = memory_pressure.parse_macos("vm.swapusage: total = 2048.00M  used = 1024.50M  free = 1023.50M (encrypted)\nkern.memorystatus_vm_pressure_level: 1\nkern.boottime: { sec = 123, usec = 456 }\nhw.memsize: 17179869184\n", 'Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages occupied by compressor: 200.\nSwapouts: 50.\n', 1000)
    assert sample["pressure"] == "normal"
    assert sample["swap_used_bytes"] == int(1024.5 * 1024 ** 2)
    assert sample["compressed_bytes"] == 200 * 16384
    assert sample["boot_time"] == 123
    assert sample["physical_bytes"] == 16 * 1024 ** 3


@pytest.mark.parametrize("flag,expected", [(2,"warning"),(4,"critical"),(3,"unknown"),(0,"unknown")])
def test_pressure_flags_do_not_confuse_internal_kernel_levels(flag, expected):
    raw = f"vm.swapusage: total = 1.00M used = 0.50M free = 0.50M\nkern.memorystatus_vm_pressure_level: {flag}\nkern.boottime: {{ sec = 1, usec = 0 }}\nhw.memsize: 1024\n"
    assert memory_pressure.parse_macos(raw, "", 100)["pressure"] == expected


def test_incomplete_required_sample_fails_loudly():
    with pytest.raises(ValueError, match="memory sample"):
        memory_pressure.parse_macos("", "", 100)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS read-only collector")
def test_real_host_sampler_is_non_destructive_and_complete():
    sample = memory_pressure.sample()
    assert sample["supported"]
    assert sample["swap_total_bytes"] >= sample["swap_used_bytes"] >= 0
    assert sample["physical_bytes"] > 0
    assert sample["boot_time"] < sample["time"]
    assert sample["pressure"] in {"normal", "warning", "critical", "unknown"}
