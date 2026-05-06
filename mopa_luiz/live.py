from __future__ import annotations

from dataclasses import dataclass
import multiprocessing as mp
from pathlib import Path
import threading
import time
from typing import Any

from galvo.controller import GalvoController

from .cli import (
    MarkConfig,
    build_test_box_plan,
    nearest_pulse_width,
)
from .safety import evaluate_emission


HARDWARE_JOB_TIMEOUT_S = 60.0
ACTIVE_HARDWARE_PROCESSES: dict[int, mp.Process] = {}
ACTIVE_HARDWARE_LOCK = threading.Lock()
STOP_REQUESTED = threading.Event()

FONT_5X7 = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10011", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}


@dataclass(frozen=True)
class JobParams:
    text: str
    power: float
    frequency_khz: float
    pulse_width_ns: float
    size_mm: float
    mark_speed: float


def _hardware_job_entry(conn, function_name: str, args: tuple, kwargs: dict) -> None:
    try:
        result = globals()[function_name](*args, **kwargs)
        conn.send({"ok": True, "result": result})
    except BaseException as exc:
        conn.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        conn.close()


def run_hardware_job(function_name: str, *args, timeout_s: float = HARDWARE_JOB_TIMEOUT_S, **kwargs) -> dict[str, Any]:
    if STOP_REQUESTED.is_set():
        raise RuntimeError("hardware stop requested")
    STOP_REQUESTED.clear()
    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_hardware_job_entry,
        args=(child_conn, function_name, args, kwargs),
    )
    process.start()
    with ACTIVE_HARDWARE_LOCK:
        ACTIVE_HARDWARE_PROCESSES[process.pid or id(process)] = process
    child_conn.close()
    message = None
    try:
        if parent_conn.poll(timeout_s):
            message = parent_conn.recv()
        else:
            process.terminate()
            process.join(3.0)
            raise TimeoutError(f"hardware job {function_name} timed out after {timeout_s:.0f}s")
    finally:
        parent_conn.close()
        with ACTIVE_HARDWARE_LOCK:
            ACTIVE_HARDWARE_PROCESSES.pop(process.pid or id(process), None)
    process.join(3.0)
    if message is None:
        raise RuntimeError(f"hardware job {function_name} ended without a result")
    if not message.get("ok"):
        raise RuntimeError(str(message.get("error") or f"hardware job {function_name} failed"))
    if process.exitcode not in (0, None):
        raise RuntimeError(f"hardware job {function_name} exited with code {process.exitcode}")
    return message["result"]


def stop_active_hardware_jobs() -> dict[str, Any]:
    STOP_REQUESTED.set()
    with ACTIVE_HARDWARE_LOCK:
        processes = list(ACTIVE_HARDWARE_PROCESSES.values())
    stopped = 0
    for process in processes:
        if process.is_alive():
            process.terminate()
            stopped += 1
    for process in processes:
        process.join(1.0)
        if process.is_alive():
            process.kill()
            process.join(1.0)
    with ACTIVE_HARDWARE_LOCK:
        ACTIVE_HARDWARE_PROCESSES.clear()
    return {"ok": True, "operation": "stop_hardware_jobs", "stopped_processes": stopped}


def clear_stop_request() -> None:
    STOP_REQUESTED.clear()


def stop_requested() -> bool:
    return STOP_REQUESTED.is_set()


def mm_to_galvo(x_mm: float, y_mm: float, field_size_mm: float) -> tuple[int, int]:
    scale = 0xFFFF / field_size_mm
    x = int(round(0x8000 + x_mm * scale))
    y = int(round(0x8000 + y_mm * scale))
    return max(0, min(0xFFFF, x)), max(0, min(0xFFFF, y))


def sanitize_polylines(polylines: list[list[list[float]]], field_size_mm: float) -> list[list[tuple[int, int]]]:
    limit = field_size_mm / 2.0
    clean: list[list[tuple[int, int]]] = []
    total_points = 0
    for polyline in polylines[:3000]:
        points = []
        for raw in polyline[:2000]:
            if len(raw) < 2:
                continue
            x = max(-limit, min(limit, float(raw[0])))
            y = max(-limit, min(limit, float(raw[1])))
            points.append(mm_to_galvo(x, y, field_size_mm))
            total_points += 1
            if total_points > 20000:
                break
        if len(points) >= 2:
            clean.append(points)
        if total_points > 20000:
            break
    if not clean:
        raise ValueError("no markable geometry in job")
    return clean


def box_points(size_mm: float, field_size_mm: float) -> list[tuple[int, int]]:
    half = size_mm / 2.0
    return [
        mm_to_galvo(-half, -half, field_size_mm),
        mm_to_galvo(half, -half, field_size_mm),
        mm_to_galvo(half, half, field_size_mm),
        mm_to_galvo(-half, half, field_size_mm),
        mm_to_galvo(-half, -half, field_size_mm),
    ]


def text_segments(text: str, size_mm: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    text = "".join(ch for ch in text.upper() if ch in FONT_5X7)[:18]
    if not text.strip():
        return []
    text_height = max(1.0, size_mm * 0.36)
    cell = text_height / 7.0
    total_width = len(text) * 6 * cell - cell
    x0 = -total_width / 2.0
    y0 = text_height / 2.0
    segments = []
    for index, char in enumerate(text):
        glyph = FONT_5X7[char]
        char_x = x0 + index * 6 * cell
        for row, bits in enumerate(glyph):
            y = y0 - row * cell
            col = 0
            while col < 5:
                if bits[col] == "0":
                    col += 1
                    continue
                start = col
                while col < 5 and bits[col] == "1":
                    col += 1
                x_start = char_x + start * cell
                x_end = char_x + col * cell - cell * 0.2
                segments.append(((x_start, y), (x_end, y)))
    return segments


def connect_controller(cfg: MarkConfig, params: JobParams) -> GalvoController:
    field_size = cfg.get_float("FIELDSIZE", 200.0) or 200.0
    controller = GalvoController(
        source="fiber",
        power=params.power,
        frequency=params.frequency_khz,
        pulse_width=nearest_pulse_width(params.pulse_width_ns),
        mark_speed=params.mark_speed,
        travel_speed=3000.0,
        light_speed=3000.0,
        galvos_per_mm=0xFFFF / field_size,
    )
    apply_wait_timeouts(controller)
    return controller


def apply_wait_timeouts(controller: GalvoController, timeout_s: float = 3.0) -> None:
    def wait_until(predicate, label: str) -> None:
        deadline = time.monotonic() + timeout_s
        while predicate():
            if time.monotonic() > deadline:
                return
            time.sleep(0.01)
            if not controller._sending:
                return

    controller.wait_idle = lambda: wait_until(controller.is_busy, "busy")  # type: ignore[method-assign]
    controller.wait_finished = lambda: wait_until(  # type: ignore[method-assign]
        lambda: not controller.is_ready_and_not_busy(), "not-ready"
    )
    controller.wait_ready = lambda: wait_until(  # type: ignore[method-assign]
        lambda: not controller.is_ready(), "not-ready"
    )


def read_board_status() -> dict[str, Any]:
    params = JobParams(
        text="",
        power=1.0,
        frequency_khz=30.0,
        pulse_width_ns=200.0,
        size_mm=10.0,
        mark_speed=100.0,
    )
    cfg = MarkConfig(path=Path("-"), values={"FIELDSIZE": "200"})
    controller = connect_controller(cfg, params)
    try:
        return {
            "version": controller.get_version(),
            "serial": controller.get_serial_number(),
            "list_status": controller.get_list_status(),
        }
    finally:
        controller.shutdown()


def frame_box(cfg: MarkConfig, params: JobParams) -> dict[str, Any]:
    evaluate_emission(
        cfg=cfg,
        power_percent=params.power,
        frequency_khz=params.frequency_khz,
        pulse_width_ns=params.pulse_width_ns,
        intends_emission=False,
        arm=False,
        confirm="",
        operation="frame_only",
        paths_count=1,
    ).raise_if_blocked()
    field_size = cfg.get_float("FIELDSIZE", 200.0) or 200.0
    points = box_points(params.size_mm, field_size)
    controller = connect_controller(cfg, params)
    try:
        def job(c: GalvoController) -> bool:
            with c.lighting():
                c.dark(*points[0])
                for point in points[1:]:
                    c.light(*point)
            return True

        controller.submit(job)
        controller.wait_for_machine_idle()
        return {
            "ok": True,
            "operation": "frame",
            "emission_enabled": False,
            "points": points,
        }
    finally:
        controller.shutdown()


class FrameLoop:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._controller: GalvoController | None = None
        self._points: list[tuple[int, int]] = []

    @property
    def running(self) -> bool:
        with self._lock:
            return self._controller is not None

    def start(self, cfg: MarkConfig, params: JobParams, polylines: list[list[list[float]]] | None = None) -> dict[str, Any]:
        evaluate_emission(
            cfg=cfg,
            power_percent=params.power,
            frequency_khz=params.frequency_khz,
            pulse_width_ns=params.pulse_width_ns,
            intends_emission=False,
            arm=False,
            confirm="",
            operation="frame_only",
            paths_count=len(polylines or []) or 1,
        ).raise_if_blocked()
        with self._lock:
            self.stop()
            field_size = cfg.get_float("FIELDSIZE", 200.0) or 200.0
            job_paths = sanitize_polylines(polylines, field_size) if polylines else [box_points(params.size_mm, field_size)]
            self._points = [pt for path in job_paths for pt in path]
            controller = connect_controller(cfg, params)

            def loop_job(c: GalvoController) -> bool:
                c.lighting_configuration()
                for path in job_paths:
                    c.dark(*path[0])
                    for point in path[1:]:
                        c.light(*point)
                return False

            controller.submit(loop_job)
            self._controller = controller
            return {
                "ok": True,
                "operation": "frame_loop_start",
                "emission_enabled": False,
                "points": self._points,
            }

    def stop(self) -> dict[str, Any]:
        controller = self._controller
        self._controller = None
        self._points = []
        if controller is not None:
            controller.shutdown()
        return {
            "ok": True,
            "operation": "frame_loop_stop",
            "emission_enabled": False,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._controller is not None,
                "points": self._points,
                "emission_enabled": False,
            }


def mark_box(cfg: MarkConfig, params: JobParams, arm: bool, confirm: str) -> dict[str, Any]:
    safety = evaluate_emission(
        cfg=cfg,
        power_percent=params.power,
        frequency_khz=params.frequency_khz,
        pulse_width_ns=params.pulse_width_ns,
        intends_emission=True,
        arm=arm,
        confirm=confirm,
        operation="vector_engrave",
        paths_count=1,
    )
    safety.raise_if_blocked()

    plan = build_test_box_plan(
        cfg,
        power=params.power,
        frequency_khz=params.frequency_khz,
        pulse_width_ns=params.pulse_width_ns,
        size_mm=params.size_mm,
        allow_power_above_1=True,
        arm=True,
    )
    field_size = cfg.get_float("FIELDSIZE", 200.0) or 200.0
    points = box_points(params.size_mm, field_size)
    controller = connect_controller(cfg, params)
    try:
        def job(c: GalvoController) -> bool:
            with c.marking():
                c.goto(*points[0])
                for point in points[1:]:
                    c.mark(*point)
                for start_mm, end_mm in text_segments(params.text, params.size_mm):
                    c.goto(*mm_to_galvo(*start_mm, field_size))
                    c.mark(*mm_to_galvo(*end_mm, field_size))
            return True

        controller.submit(job)
        controller.wait_for_machine_idle()
        return {
            "ok": True,
            "operation": "mark",
            "emission_enabled": True,
            "plan": plan,
            "points": points,
            "text": params.text,
        }
    finally:
        controller.shutdown()


def frame_polylines(cfg: MarkConfig, params: JobParams, polylines: list[list[list[float]]]) -> dict[str, Any]:
    evaluate_emission(
        cfg=cfg,
        power_percent=params.power,
        frequency_khz=params.frequency_khz,
        pulse_width_ns=params.pulse_width_ns,
        intends_emission=False,
        arm=False,
        confirm="",
        operation="frame_only",
        paths_count=len(polylines or []),
    ).raise_if_blocked()
    field_size = cfg.get_float("FIELDSIZE", 200.0) or 200.0
    job_paths = sanitize_polylines(polylines, field_size)
    controller = connect_controller(cfg, params)
    try:
        def job(c: GalvoController) -> bool:
            with c.lighting():
                for path in job_paths:
                    c.dark(*path[0])
                    for point in path[1:]:
                        c.light(*point)
            return True

        controller.submit(job)
        controller.wait_for_machine_idle()
        return {
            "ok": True,
            "operation": "frame_geometry",
            "emission_enabled": False,
            "paths": len(job_paths),
            "points": sum(len(path) for path in job_paths),
        }
    finally:
        controller.shutdown()


def mark_polylines(
    cfg: MarkConfig,
    params: JobParams,
    polylines: list[list[list[float]]],
    arm: bool,
    confirm: str,
    operation: str = "vector_engrave",
) -> dict[str, Any]:
    safety = evaluate_emission(
        cfg=cfg,
        power_percent=params.power,
        frequency_khz=params.frequency_khz,
        pulse_width_ns=params.pulse_width_ns,
        intends_emission=True,
        arm=arm,
        confirm=confirm,
        operation=operation,
        paths_count=len(polylines or []),
    )
    safety.raise_if_blocked()
    field_size = cfg.get_float("FIELDSIZE", 200.0) or 200.0
    job_paths = sanitize_polylines(polylines, field_size)
    controller = connect_controller(cfg, params)
    try:
        def job(c: GalvoController) -> bool:
            with c.marking():
                for path in job_paths:
                    c.goto(*path[0])
                    for point in path[1:]:
                        c.mark(*point)
            return True

        controller.submit(job)
        controller.wait_for_machine_idle()
        return {
            "ok": True,
            "operation": "mark_geometry",
            "emission_enabled": True,
            "power_percent": params.power,
            "frequency_khz": params.frequency_khz,
            "effective_pulse_width_ns": nearest_pulse_width(params.pulse_width_ns),
            "paths": len(job_paths),
            "points": sum(len(path) for path in job_paths),
        }
    finally:
        controller.shutdown()
