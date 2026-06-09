"""
Analyze the raw experimental data and produce:
  - data/processed/summary_stats.csv
  - analysis/plots/*.png
  - analysis/results_summary.md

This script is intentionally idempotent: re-running it overwrites the
outputs. It accepts data produced by either benchmark/local_simulator.py
or benchmark/load_test.py (same schema).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend; safe for headless runs
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
PLOTS = ROOT / "analysis" / "plots"
PROC.mkdir(parents=True, exist_ok=True)
PLOTS.mkdir(parents=True, exist_ok=True)


# Stable, restrained palette so the figures read well in print.
COLOR_DOCKER = "#1f77b4"
COLOR_SERVERLESS = "#d62728"
COLOR_COLD = "#9467bd"
COLOR_WARM = "#2ca02c"


def _pct(s: pd.Series, p: float) -> float:
    return float(np.percentile(s, p))


def summarize(lat: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (arch, scenario), g in lat.groupby(["arch", "scenario"], sort=False):
        rows.append({
            "arch": arch,
            "scenario": scenario,
            "n": len(g),
            "mean_ms": round(g["latency_ms"].mean(), 2),
            "median_ms": round(g["latency_ms"].median(), 2),
            "p95_ms": round(_pct(g["latency_ms"], 95), 2),
            "p99_ms": round(_pct(g["latency_ms"], 99), 2),
            "stddev_ms": round(g["latency_ms"].std(), 2),
            "min_ms": round(g["latency_ms"].min(), 2),
            "max_ms": round(g["latency_ms"].max(), 2),
        })
        # Split cold vs warm for serverless if applicable
        if arch == "serverless" and g["cold_start"].any():
            cold = g[g["cold_start"]]
            warm = g[~g["cold_start"]]
            rows.append({
                "arch": "serverless_cold",
                "scenario": scenario,
                "n": len(cold),
                "mean_ms": round(cold["latency_ms"].mean(), 2) if len(cold) else None,
                "median_ms": round(cold["latency_ms"].median(), 2) if len(cold) else None,
                "p95_ms": round(_pct(cold["latency_ms"], 95), 2) if len(cold) else None,
                "p99_ms": round(_pct(cold["latency_ms"], 99), 2) if len(cold) else None,
                "stddev_ms": round(cold["latency_ms"].std(), 2) if len(cold) > 1 else None,
                "min_ms": round(cold["latency_ms"].min(), 2) if len(cold) else None,
                "max_ms": round(cold["latency_ms"].max(), 2) if len(cold) else None,
            })
            rows.append({
                "arch": "serverless_warm",
                "scenario": scenario,
                "n": len(warm),
                "mean_ms": round(warm["latency_ms"].mean(), 2) if len(warm) else None,
                "median_ms": round(warm["latency_ms"].median(), 2) if len(warm) else None,
                "p95_ms": round(_pct(warm["latency_ms"], 95), 2) if len(warm) else None,
                "p99_ms": round(_pct(warm["latency_ms"], 99), 2) if len(warm) else None,
                "stddev_ms": round(warm["latency_ms"].std(), 2) if len(warm) > 1 else None,
                "min_ms": round(warm["latency_ms"].min(), 2) if len(warm) else None,
                "max_ms": round(warm["latency_ms"].max(), 2) if len(warm) else None,
            })
    return pd.DataFrame(rows)


def summarize_resources(res: pd.DataFrame) -> pd.DataFrame:
    rows = []
    # Drop zero-pid samples (worker not yet started or torn down)
    res = res[res["pid"] > 0].copy()
    for (arch, scenario), g in res.groupby(["arch", "scenario"], sort=False):
        rows.append({
            "arch": arch,
            "scenario": scenario,
            "samples": len(g),
            "cpu_mean_pct": round(g["cpu_percent"].mean(), 2),
            "cpu_p95_pct": round(_pct(g["cpu_percent"], 95), 2),
            "rss_mean_mb": round(g["rss_mb"].mean(), 2),
            "rss_p95_mb": round(_pct(g["rss_mb"], 95), 2),
        })
    return pd.DataFrame(rows)


# -- plots --------------------------------------------------------------

def plot_latency_by_scenario(lat: pd.DataFrame, out: Path) -> None:
    scenarios = ["light_load", "medium_load", "heavy_load"]
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=140)

    data_docker = [lat[(lat.arch == "docker") & (lat.scenario == s)]["latency_ms"].values
                   for s in scenarios]
    data_serverless = [lat[(lat.arch == "serverless") & (lat.scenario == s) & (~lat.cold_start)]
                       ["latency_ms"].values for s in scenarios]

    positions_d = np.arange(len(scenarios)) - 0.18
    positions_s = np.arange(len(scenarios)) + 0.18

    bp_d = ax.boxplot(data_docker, positions=positions_d, widths=0.3,
                      patch_artist=True, showfliers=False)
    bp_s = ax.boxplot(data_serverless, positions=positions_s, widths=0.3,
                      patch_artist=True, showfliers=False)

    for box in bp_d["boxes"]:
        box.set_facecolor(COLOR_DOCKER); box.set_alpha(0.75); box.set_edgecolor("black")
    for box in bp_s["boxes"]:
        box.set_facecolor(COLOR_SERVERLESS); box.set_alpha(0.75); box.set_edgecolor("black")
    for bp in (bp_d, bp_s):
        for med in bp["medians"]:
            med.set_color("black"); med.set_linewidth(1.5)

    ax.set_xticks(np.arange(len(scenarios)))
    ax.set_xticklabels([s.replace("_", " ").title() for s in scenarios])
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Per-request latency: Docker vs Serverless (warm only)")
    ax.legend([bp_d["boxes"][0], bp_s["boxes"][0]], ["Docker", "Serverless (warm)"],
              loc="upper left")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def plot_cold_vs_warm(lat: pd.DataFrame, out: Path) -> None:
    probe = lat[lat.scenario == "cold_warm_probe"]
    cold = probe[(probe.arch == "serverless") & (probe.cold_start)]["latency_ms"].values
    warm = probe[(probe.arch == "serverless") & (~probe.cold_start)]["latency_ms"].values
    docker = probe[probe.arch == "docker"]["latency_ms"].values

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=140)
    parts = ax.violinplot(
        [docker, warm, cold],
        positions=[0, 1, 2],
        showmedians=True,
        widths=0.7,
    )
    colors = [COLOR_DOCKER, COLOR_WARM, COLOR_COLD]
    for body, c in zip(parts["bodies"], colors):
        body.set_facecolor(c); body.set_edgecolor("black"); body.set_alpha(0.7)
    for key in ("cmedians", "cbars", "cmins", "cmaxes"):
        if key in parts:
            parts[key].set_color("black")

    medians = [np.median(docker), np.median(warm), np.median(cold)]
    for x, m in enumerate(medians):
        ax.annotate(f"med={m:.1f}ms", (x, m), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=9)

    ax.set_xticks([0, 1, 2])
    ax.set_xticklabels(["Docker", "Serverless warm", "Serverless cold"])
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Cold vs warm vs always-on (probe scenario, light workload)")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def plot_latency_timeseries(lat: pd.DataFrame, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=140)
    probe = lat[lat.scenario == "cold_warm_probe"].copy()

    docker = probe[probe.arch == "docker"].reset_index(drop=True)
    serv = probe[probe.arch == "serverless"].reset_index(drop=True)

    ax.plot(docker.index, docker["latency_ms"], "-o", color=COLOR_DOCKER,
            label="Docker", markersize=4, linewidth=1.2)
    ax.plot(serv.index, serv["latency_ms"], "-o", color=COLOR_SERVERLESS,
            label="Serverless", markersize=4, linewidth=1.2)

    cold_idx = serv[serv["cold_start"]].index
    ax.scatter(cold_idx, serv.loc[cold_idx, "latency_ms"], s=80,
               facecolors="none", edgecolors=COLOR_COLD, linewidths=1.6,
               label="Serverless cold start", zorder=5)

    ax.set_xlabel("Request index")
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Latency over time — cold/warm probe scenario")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def plot_resource_usage(res: pd.DataFrame, out_cpu: Path, out_mem: Path) -> None:
    scenarios = ["light_load", "medium_load", "heavy_load"]
    res = res[res["pid"] > 0]

    # CPU
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=140)
    x = np.arange(len(scenarios))
    w = 0.35
    docker_cpu = [res[(res.arch == "docker") & (res.scenario == s)]["cpu_percent"].mean()
                  for s in scenarios]
    serv_cpu = [res[(res.arch == "serverless") & (res.scenario == s)]["cpu_percent"].mean()
                for s in scenarios]
    ax.bar(x - w/2, docker_cpu, w, color=COLOR_DOCKER, label="Docker", edgecolor="black")
    ax.bar(x + w/2, serv_cpu, w, color=COLOR_SERVERLESS, label="Serverless", edgecolor="black")
    ax.set_xticks(x)
    ax.set_xticklabels([s.replace("_", " ").title() for s in scenarios])
    ax.set_ylabel("Mean CPU usage (%)")
    ax.set_title("Mean CPU utilisation per scenario (during active processing)")
    ax.legend(); ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout(); fig.savefig(out_cpu); plt.close(fig)

    # Memory
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=140)
    docker_rss = [res[(res.arch == "docker") & (res.scenario == s)]["rss_mb"].mean()
                  for s in scenarios]
    serv_rss = [res[(res.arch == "serverless") & (res.scenario == s)]["rss_mb"].mean()
                for s in scenarios]
    ax.bar(x - w/2, docker_rss, w, color=COLOR_DOCKER, label="Docker", edgecolor="black")
    ax.bar(x + w/2, serv_rss, w, color=COLOR_SERVERLESS, label="Serverless", edgecolor="black")
    ax.set_xticks(x)
    ax.set_xticklabels([s.replace("_", " ").title() for s in scenarios])
    ax.set_ylabel("Mean RSS (MB)")
    ax.set_title("Mean memory footprint per scenario (during active processing)")
    ax.legend(); ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout(); fig.savefig(out_mem); plt.close(fig)


def plot_always_on_overhead(res_raw: pd.DataFrame, out: Path) -> None:
    """Time-integrated process footprint across the full probe scenario.

    Captures the "always-on" cost: Docker keeps a worker (and therefore
    its resident memory) alive for 100% of wallclock time; the serverless
    worker only exists during the warm window after an invocation.
    """
    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=140)
    probe = res_raw[res_raw.scenario == "cold_warm_probe"].copy()
    if probe.empty:
        plt.close(fig)
        return

    for arch, color in (("docker", COLOR_DOCKER), ("serverless", COLOR_SERVERLESS)):
        g = probe[probe.arch == arch].sort_values("ts")
        if g.empty:
            continue
        t0 = g["ts"].iloc[0]
        t_rel = g["ts"] - t0
        ax.plot(t_rel, g["rss_mb"], "-", color=color, label=arch.title(), linewidth=1.4)

    ax.set_xlabel("Time since scenario start (s)")
    ax.set_ylabel("Worker RSS (MB)")
    ax.set_title("Process memory footprint over time — cold/warm probe scenario\n"
                 "(serverless drops to 0 when the worker is recycled)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)



def write_summary_md(lat_summary: pd.DataFrame, res_summary: pd.DataFrame, out: Path) -> None:
    lines = ["# Experiment results summary\n"]
    lines.append("## Latency (ms)\n")
    lines.append(lat_summary.to_markdown(index=False))
    lines.append("\n\n## Resource utilisation\n")
    lines.append(res_summary.to_markdown(index=False))
    lines.append("\n")
    out.write_text("\n".join(lines))


def main() -> None:
    lat = pd.read_csv(RAW / "latency.csv")
    res = pd.read_csv(RAW / "resources.csv")

    # cold_start may have been written as "True"/"False" strings
    if lat["cold_start"].dtype == object:
        lat["cold_start"] = lat["cold_start"].map(lambda v: str(v).lower() == "true")

    lat_summary = summarize(lat)
    res_summary = summarize_resources(res)

    lat_summary.to_csv(PROC / "latency_summary.csv", index=False)
    res_summary.to_csv(PROC / "resource_summary.csv", index=False)

    plot_latency_by_scenario(lat, PLOTS / "latency_by_scenario.png")
    plot_cold_vs_warm(lat, PLOTS / "cold_vs_warm.png")
    plot_latency_timeseries(lat, PLOTS / "latency_timeseries_probe.png")
    plot_resource_usage(res, PLOTS / "cpu_usage.png", PLOTS / "memory_usage.png")
    plot_always_on_overhead(res, PLOTS / "always_on_overhead.png")

    write_summary_md(lat_summary, res_summary, ROOT / "analysis" / "results_summary.md")

    print("[ok] wrote summaries and plots")
    print(lat_summary.to_string(index=False))
    print()
    print(res_summary.to_string(index=False))


if __name__ == "__main__":
    main()
