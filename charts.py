import datetime
import os
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

plt.rcParams.update({
    "figure.facecolor": "#0f172a", "axes.facecolor": "#0f172a",
    "axes.edgecolor": "#475569", "text.color": "white",
    "xtick.color": "#cbd5e1", "ytick.color": "#cbd5e1",
    "font.size": 10,
})


def _tmp() -> str:
    fd, path = tempfile.mkstemp(suffix=".png", prefix="dewflow_chart_")
    os.close(fd)
    return path


def growth_chart(history: list[dict], title: str) -> str:
    path = _tmp()
    fig, ax = plt.subplots(figsize=(8, 4.2))
    if len(history) < 2:
        ax.text(0.5, 0.5, "Нужно минимум 2 снимка\n(повтори парсинг позже)",
                ha="center", va="center", transform=ax.transAxes, color="#94a3b8")
        ax.set_title(f"Рост: {title}", color="white")
    else:
        xs = [datetime.datetime.fromtimestamp(h["finished_at"]) for h in history]
        ys = [h["seen"] for h in history]
        ax.plot(xs, ys, marker="o", color="#22d3ee", linewidth=2)
        ax.fill_between(xs, ys, alpha=0.15, color="#22d3ee")
        ax.set_title(f"Динамика: {title}", color="white")
        ax.set_ylabel("участников", color="#cbd5e1")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
        fig.autofmt_xdate(rotation=25)
        delta = ys[-1] - ys[0]
        ax.annotate(f"{ys[-1]} ({delta:+})", xy=(xs[-1], ys[-1]),
                    xytext=(6, 8), textcoords="offset points", color="#a5f3fc")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def segments_pie(seg: dict) -> str:
    path = _tmp()
    total = max(seg.get("total", 0), 1)
    labels = ["username", "с фото", "Premium", "с номером", "в 2+ чатах"]
    vals = [seg.get("username", 0), seg.get("photo", 0), seg.get("premium", 0),
            seg.get("phone", 0), seg.get("multi", 0)]
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    if sum(vals) == 0:
        ax.text(0.5, 0.5, "База пуста", ha="center", va="center",
                transform=ax.transAxes, color="#94a3b8")
    else:
        colors = ["#22d3ee", "#a78bfa", "#fbbf24", "#34d399", "#f472b6"]
        wedges, _, autot = ax.pie(vals, autopct=lambda p: f"{p:.0f}%" if p > 4 else "",
                                  colors=colors, startangle=90,
                                  textprops={"color": "white", "fontsize": 9})
        ax.legend(wedges, [f"{l} — {v}" for l, v in zip(labels, vals)],
                  loc="center left", bbox_to_anchor=(1, 0.5), frameon=False,
                  labelcolor="white")
        ax.set_title(f"Сегменты базы (всего {total})", color="white")
    fig.tight_layout()
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path
