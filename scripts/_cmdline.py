# -*- coding: utf-8 -*-
"""读某个 pid 的命令行（wmic 被沙箱拉黑，改用 PEB + NtQueryInformationProcess）。"""
import ctypes
import ctypes.wintypes as wintypes
import sys

ntdll = ctypes.WinDLL("ntdll")
kernel32 = ctypes.WinDLL("kernel32")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010


def read_command_line(pid):
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        return None
    try:
        # 用 WMI-free 的简化法：读 PEB 里的 ProcessParameters
        class PROCESS_BASIC_INFORMATION(ctypes.Structure):
            _fields_ = [("Reserved1", ctypes.c_void_p),
                        ("PebBaseAddress", ctypes.c_void_p),
                        ("Reserved2", ctypes.c_void_p * 2),
                        ("UniqueProcessId", ctypes.c_void_p),
                        ("Reserved3", ctypes.c_void_p)]

        pbi = PROCESS_BASIC_INFORMATION()
        ret_len = ctypes.c_ulong(0)
        ntdll.NtQueryInformationProcess(handle, 0, ctypes.byref(pbi),
                                        ctypes.sizeof(pbi), ctypes.byref(ret_len))
        peb = pbi.PebBaseAddress
        if not peb:
            return None

        def rpm(addr, size):
            buf = ctypes.create_string_buffer(size)
            read = ctypes.c_size_t(0)
            ok = kernel32.ReadProcessMemory(handle, ctypes.c_void_p(addr), buf,
                                            size, ctypes.byref(read))
            return buf.raw if ok else None

        # PEB->ProcessParameters 在 64 位下偏移 0x20
        raw = rpm(peb + 0x20, 8)
        if not raw:
            return None
        params = int.from_bytes(raw, "little")
        # RTL_USER_PROCESS_PARAMETERS->CommandLine (UNICODE_STRING) 偏移 0x70
        raw = rpm(params + 0x70, 16)
        if not raw:
            return None
        length = int.from_bytes(raw[0:2], "little")
        buffer_ptr = int.from_bytes(raw[8:16], "little")
        if not length or not buffer_ptr:
            return None
        raw = rpm(buffer_ptr, min(length, 4096))
        if not raw:
            return None
        return raw.decode("utf-16-le", "replace")
    except Exception as error:
        return "ERR %s" % error
    finally:
        kernel32.CloseHandle(handle)


for pid in [int(a) for a in sys.argv[1:]]:
    print("pid %-7d %s" % (pid, (read_command_line(pid) or "?")[:300]))
