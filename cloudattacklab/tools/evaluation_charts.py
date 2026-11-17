#!/usr/bin/env python3
"""Charts for docs/evaluation-results.md, from a results directory of tools/evaluate.py.

    python3 tools/evaluation_charts.py evaluation/results/2026-11-17 docs/evaluation

Needs matplotlib. Writes PNG files only; every number comes from the raw CSVs.
"""
from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SOURCE_COLOR = {"docker": "#2a78d6", "loki": "#eb6834"}  # categorical slots 1 and 2
TEXT, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
SHORT = {"authentication_abuse": "authentication\nabuse", "unauthorized_resource_access": "unauthorized\nresource access",
         "suspicious_container_activity": "suspicious\ncontainer activity"}


def style(ax, title: str, xlabel: str = "", ylabel: str = "") -> None:
    ax.set_title(title, loc="left", color=TEXT, fontsize=12, pad=12)
    ax.set_xlabel(xlabel, color=MUTED)
    ax.set_ylabel(ylabel, color=MUTED)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED)
    ax.set_axisbelow(True)


def read(path: Path) -> list[dict]:
    return list(csv.DictReader(path.open())) if path.exists() else []


def latency_chart(results: Path, out: Path) -> None:
    rows = [r for r in read(results / "runs.csv") if r["detection_latency_s"]]
    scenarios = [s for s in SHORT if any(r["scenario_id"] == s for r in rows)]
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    for i, scenario in enumerate(scenarios):
        for j, source in enumerate(("docker", "loki")):
            values = [float(r["detection_latency_s"]) for r in rows
                      if r["scenario_id"] == scenario and r["telemetry_source"] == source]
            if not values:
                continue
            x = i + (j - 0.5) * 0.3
            ax.scatter([x] * len(values), values, s=36, color=SOURCE_COLOR[source], alpha=0.75,
                       edgecolor="white", linewidth=1, zorder=3, label=source if i == 0 else None)
            median = statistics.median(values)
            ax.plot([x - 0.1, x + 0.1], [median, median], color=TEXT, linewidth=2, zorder=4)
    ax.set_xticks(range(len(scenarios)), [SHORT[s] for s in scenarios])
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    style(ax, "Detection latency per run (dots) and median (bar)", ylabel="seconds")
    ax.legend(title="telemetry source", frameon=False)
    fig.tight_layout()
    fig.savefig(out / "detection-latency.png")
    plt.close(fig)


def setup_chart(results: Path, out: Path) -> None:
    rows = [r for r in read(results / "lab_setup.csv") if r.get("ready_seconds")]
    groups = {}
    for r in rows:
        groups.setdefault((r["operation"], r["telemetry_source"]), []).append(float(r["ready_seconds"]))
    labels = [k for k in [("start", "docker"), ("start", "loki"), ("reset", "docker"), ("reset", "loki")] if k in groups]
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=150)
    for i, key in enumerate(labels):
        values = groups[key]
        median = statistics.median(values)
        ax.barh(i, median, height=0.5, color=SOURCE_COLOR[key[1]], zorder=3)
        ax.plot([min(values), max(values)], [i, i], color=TEXT, linewidth=1.5, zorder=4)
        ax.text(max(values) + 0.5, i, f"median {median:.1f}s (n={len(values)})", va="center", color=MUTED, fontsize=9)
    names = {("start", "docker"): "start, Falco off", ("start", "loki"): "start, Falco on",
             ("reset", "docker"): "reset, Falco off", ("reset", "loki"): "reset, Falco on"}
    ax.set_yticks(range(len(labels)), [names[k] for k in labels])
    ax.invert_yaxis()
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_xlim(0, max(max(v) for v in groups.values()) * 1.45)
    style(ax, "Lab start and reset: request to healthy target (bar = median, line = min to max)", xlabel="seconds")
    fig.tight_layout()
    fig.savefig(out / "lab-setup-time.png")
    plt.close(fig)


def memory_chart(results: Path, out: Path) -> None:
    rows = read(results / "docker_stats.csv")
    peak: dict[tuple[str, str], float] = {}
    for r in rows:
        if r["phase"] not in ("docker", "loki"):
            continue
        name = r["container"].replace("cloudattacklab-", "").removesuffix("-1")
        key = (name, r["telemetry_source"])
        peak[key] = max(peak.get(key, 0.0), float(r["mem_mib"]))
    containers = sorted({k[0] for k in peak}, key=lambda c: -max(peak.get((c, s), 0) for s in ("docker", "loki")))
    fig, ax = plt.subplots(figsize=(8, 0.5 * len(containers) + 1.4), dpi=150)
    height = 0.36
    for i, container in enumerate(containers):
        for j, source in enumerate(("docker", "loki")):
            if (container, source) in peak:
                value = peak[(container, source)]
                y = i + (j - 0.5) * (height + 0.04)
                ax.barh(y, value, height=height, color=SOURCE_COLOR[source], zorder=3,
                        label=source if i == 0 else None)
                ax.text(value + 5, y, f"{value:.0f}", va="center", color=MUTED, fontsize=8)
    ax.set_yticks(range(len(containers)), containers)
    ax.invert_yaxis()
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    style(ax, "Peak memory per container during scenario runs", xlabel="MiB")
    ax.legend(title="phase (telemetry source)", frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "peak-memory.png")
    plt.close(fig)


def main() -> None:
    results, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    latency_chart(results, out)
    setup_chart(results, out)
    memory_chart(results, out)
    print(f"charts written to {out}")


if __name__ == "__main__":
    main()
