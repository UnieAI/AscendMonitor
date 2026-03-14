#!/usr/bin/env python3
import curses
import subprocess
import threading
import queue
import time
from collections import defaultdict, deque

FIELDS = [
    "NpuID(Idx)",
    "ChipId(Idx)",
    "Pwr(W)",
    "Temp(C)",
    "AI Core(%)",
    "AI Cpu(%)",
    "Ctrl Cpu(%)",
    "Memory(%)",
    "Memory BW(%)",
    "NPU Util(%)",
    "AI Cube(%)",
]

def parse_data_line(line: str):
    parts = line.strip().split()
    if len(parts) != 11:
        return None
    try:
        return {
            "NpuID(Idx)": int(parts[0]),
            "ChipId(Idx)": int(parts[1]),
            "Pwr(W)": float(parts[2]),
            "Temp(C)": int(parts[3]),
            "AI Core(%)": int(parts[4]),
            "AI Cpu(%)": int(parts[5]),
            "Ctrl Cpu(%)": int(parts[6]),
            "Memory(%)": int(parts[7]),
            "Memory BW(%)": int(parts[8]),
            "NPU Util(%)": int(parts[9]),
            "AI Cube(%)": int(parts[10]),
        }
    except ValueError:
        return None

def reader_thread(proc, out_q):
    for line in iter(proc.stdout.readline, ''):
        out_q.put(line.rstrip("\n"))
    out_q.put(None)

def safe_add(stdscr, y, x, text, attr=0):
    h, w = stdscr.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    try:
        stdscr.addnstr(y, x, str(text), max(0, w - x - 1), attr)
    except curses.error:
        pass

def bar(value, max_value=100, width=12):
    value = max(0, min(value, max_value))
    filled = int(round((value / max_value) * width))
    return "█" * filled + "·" * (width - filled)

def sparkline(values, width=16):
    ticks = "▁▂▃▄▅▆▇█"
    vals = list(values)[-width:]
    if not vals:
        return ""
    vmin = min(vals)
    vmax = max(vals)
    if vmax == vmin:
        return ticks[0] * len(vals)
    out = []
    for v in vals:
        idx = int((v - vmin) / (vmax - vmin) * (len(ticks) - 1))
        out.append(ticks[idx])
    return "".join(out)

def init_colors():
    if not curses.has_colors():
        return
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_RED, -1)
    curses.init_pair(5, curses.COLOR_MAGENTA, -1)

def temp_attr(v):
    if not curses.has_colors():
        return curses.A_NORMAL
    if v >= 80:
        return curses.color_pair(4) | curses.A_BOLD
    if v >= 65:
        return curses.color_pair(3) | curses.A_BOLD
    return curses.color_pair(2)

def util_attr(v):
    if not curses.has_colors():
        return curses.A_NORMAL
    if v >= 85:
        return curses.color_pair(4) | curses.A_BOLD
    if v >= 60:
        return curses.color_pair(3) | curses.A_BOLD
    if v > 0:
        return curses.color_pair(2)
    return curses.A_DIM

def pwr_attr(v):
    if not curses.has_colors():
        return curses.A_NORMAL
    if v >= 180:
        return curses.color_pair(3) | curses.A_BOLD
    return curses.A_NORMAL

def draw(stdscr, devices, pwr_hist, util_hist, last_update, snapshot_count, status):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    safe_add(stdscr, 0, 0, "Ascend NPU TUI  |  q quit  r reset counters", curses.A_BOLD)
    safe_add(stdscr, 1, 0, f"Last update: {last_update or 'waiting'}   Snapshots: {snapshot_count}", curses.A_DIM)
    safe_add(stdscr, 2, 0, status, curses.A_DIM)

    header = (
        f"{'NPU':<4} {'Pwr':>6} {'Tmp':>4} {'Util':>5} {'Mem':>4} {'MBW':>4} "
        f"{'Core':>5} {'Cube':>5}  {'Util Bar':<12}  {'Power Trend':<16}  {'Util Trend':<16}"
    )
    safe_add(stdscr, 4, 0, header, curses.A_BOLD | curses.A_UNDERLINE)

    y = 5
    for npu_id in sorted(devices):
        d = devices[npu_id]
        util = d["NPU Util(%)"]
        temp = d["Temp(C)"]
        pwr = d["Pwr(W)"]
        mem = d["Memory(%)"]
        mbw = d["Memory BW(%)"]
        core = d["AI Core(%)"]
        cube = d["AI Cube(%)"]

        safe_add(stdscr, y, 0, f"{npu_id:<4}", curses.A_BOLD)
        safe_add(stdscr, y, 5, f"{pwr:>6.1f}", pwr_attr(pwr))
        safe_add(stdscr, y, 12, f"{temp:>4}", temp_attr(temp))
        safe_add(stdscr, y, 17, f"{util:>5}", util_attr(util))
        safe_add(stdscr, y, 23, f"{mem:>4}")
        safe_add(stdscr, y, 28, f"{mbw:>4}", util_attr(mbw))
        safe_add(stdscr, y, 33, f"{core:>5}", util_attr(core))
        safe_add(stdscr, y, 39, f"{cube:>5}", util_attr(cube))
        safe_add(stdscr, y, 46, bar(util, 100, 12), util_attr(util))
        safe_add(stdscr, y, 61, sparkline(pwr_hist[npu_id], 16), pwr_attr(pwr))
        safe_add(stdscr, y, 80, sparkline(util_hist[npu_id], 16), util_attr(util))
        y += 1
        if y >= h - 3:
            break

    if devices:
        total_power = sum(d["Pwr(W)"] for d in devices.values())
        avg_temp = sum(d["Temp(C)"] for d in devices.values()) / len(devices)
        hottest = max(devices.values(), key=lambda x: x["Temp(C)"])
        busiest = max(devices.values(), key=lambda x: x["NPU Util(%)"])
        footer = (
            f"Total Power: {total_power:.1f}W   Avg Temp: {avg_temp:.1f}C   "
            f"Hottest: NPU {hottest['NpuID(Idx)']} {hottest['Temp(C)']}C   "
            f"Busiest: NPU {busiest['NpuID(Idx)']} {busiest['NPU Util(%)']}%"
        )
        safe_add(stdscr, h - 2, 0, footer, curses.A_BOLD)
    else:
        safe_add(stdscr, h - 2, 0, "No device data yet.", curses.A_DIM)

    stdscr.refresh()

def main_loop(stdscr):
    curses.curs_set(0)
    stdscr.timeout(200)
    stdscr.keypad(True)
    init_colors()

    proc = subprocess.Popen(
        ["npu-smi", "info", "watch"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    out_q = queue.Queue()
    threading.Thread(target=reader_thread, args=(proc, out_q), daemon=True).start()

    devices = {}
    current_rows = {}
    last_npu_id = None
    last_update = ""
    snapshot_count = 0
    status = "Starting npu-smi info watch..."

    pwr_hist = defaultdict(lambda: deque(maxlen=30))
    util_hist = defaultdict(lambda: deque(maxlen=30))

    while True:
        key = stdscr.getch()
        if key in (ord('q'), ord('Q')):
            break
        if key in (ord('r'), ord('R')):
            snapshot_count = 0
            pwr_hist.clear()
            util_hist.clear()
            status = "Counters and chart history reset."

        try:
            while True:
                line = out_q.get_nowait()

                if line is None:
                    status = "npu-smi exited."
                    break

                s = line.strip()
                if not s:
                    continue
                if s.startswith("NpuID(Idx)"):
                    status = "Header received."
                    continue
                if s.startswith("npu-smi info watch"):
                    continue

                row = parse_data_line(s)
                if row is None:
                    status = f"Skipped unparsable line: {s[:70]}"
                    continue

                npu_id = row["NpuID(Idx)"]

                if last_npu_id is not None and npu_id <= last_npu_id:
                    devices = dict(sorted(current_rows.items()))
                    current_rows = {}
                    snapshot_count += 1
                    last_update = time.strftime("%Y-%m-%d %H:%M:%S")
                    status = f"Snapshot #{snapshot_count}   Devices: {len(devices)}"

                current_rows[npu_id] = row
                pwr_hist[npu_id].append(row["Pwr(W)"])
                util_hist[npu_id].append(row["NPU Util(%)"])
                last_npu_id = npu_id

        except queue.Empty:
            pass

        preview = devices.copy()
        preview.update(current_rows)
        draw(stdscr, preview, pwr_hist, util_hist, last_update, snapshot_count, status)

    try:
        proc.terminate()
        proc.wait(timeout=1)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

def main():
    curses.wrapper(main_loop)

if __name__ == "__main__":
    main()