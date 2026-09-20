from __future__ import annotations


COLORS = {
    "green": "#2E6654",
    "sage": "#78A38F",
    "red": "#AD5A58",
    "amber": "#B28A52",
    "dark_green": "#183F35",
    "other": "#A5AFA9",
    "grid": "#E3E8E4",
    "text": "#69756F",
}


def _base_grid(left: int = 52, right: int = 18, bottom: int = 40, top: int = 22) -> dict:
    return {"left": left, "right": right, "bottom": bottom, "top": top, "containLabel": True}


def trend_option(categories: list[str], series: dict[str, list[int]]) -> dict:
    color_map = {"Full": COLORS["dark_green"], "Empty": COLORS["sage"], "Transhipment": COLORS["amber"]}
    return {
        "animationDuration": 220,
        "color": list(color_map.values()),
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 0, "itemWidth": 10, "itemHeight": 10, "textStyle": {"color": COLORS["text"]}},
        "grid": _base_grid(bottom=48, top=16),
        "xAxis": {
            "type": "category",
            "boundaryGap": False,
            "data": categories,
            "axisLine": {"lineStyle": {"color": "#CDD7D1"}},
            "axisTick": {"show": False},
            "axisLabel": {"color": COLORS["text"]},
        },
        "yAxis": {
            "type": "value",
            "name": "TEU",
            "nameTextStyle": {"color": COLORS["text"]},
            "splitLine": {"lineStyle": {"color": COLORS["grid"]}},
            "axisLabel": {"color": COLORS["text"]},
        },
        "series": [
            {
                "name": name,
                "type": "line",
                "smooth": True,
                "symbol": "circle",
                "symbolSize": 7,
                "lineStyle": {"width": 3},
                "areaStyle": {"opacity": 0.04},
                "data": values,
            }
            for name, values in series.items()
        ],
    }


def cargo_option(categories: list[str], values: list[int], mode: str = "Muat") -> dict:
    bar_color = COLORS["green"] if mode == "Muat" else COLORS["red"]
    return {
        "animationDuration": 220,
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "grid": _base_grid(left=18, right=58, bottom=18, top=12),
        "xAxis": {
            "type": "value",
            "splitLine": {"lineStyle": {"color": COLORS["grid"]}},
            "axisLabel": {"color": COLORS["text"]},
        },
        "yAxis": {
            "type": "category",
            "data": categories,
            "axisTick": {"show": False},
            "axisLine": {"show": False},
            "axisLabel": {"color": COLORS["dark_green"], "width": 120, "overflow": "truncate"},
        },
        "series": [
            {
                "type": "bar",
                "data": values,
                "barWidth": 17,
                "itemStyle": {"color": bar_color, "borderRadius": [0, 2, 2, 0]},
                "label": {"show": True, "position": "right", "color": COLORS["dark_green"], "formatter": "{c}"},
            }
        ],
    }


def composition_option(categories: list[str], series: dict[str, list[int]], view: str = "Grade") -> dict:
    if view == "Grade":
        color_map = {"A": COLORS["dark_green"], "B": COLORS["sage"], "C": COLORS["amber"], "Other": COLORS["other"]}
        labels = {"A": "Grade A", "B": "Grade B", "C": "Grade C", "Other": "Other/Unknown"}
    else:
        color_map = {"20": COLORS["dark_green"], "40": COLORS["sage"], "Other": COLORS["other"]}
        labels = {"20": "20 ft", "40": "40 ft", "Other": "Other/Unknown"}
    return {
        "animationDuration": 220,
        "color": [color_map[name] for name in series],
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"bottom": 0, "itemWidth": 10, "itemHeight": 10, "textStyle": {"color": COLORS["text"]}},
        "grid": _base_grid(bottom=48, top=24),
        "xAxis": {
            "type": "category",
            "data": categories,
            "axisTick": {"show": False},
            "axisLine": {"lineStyle": {"color": "#CDD7D1"}},
            "axisLabel": {"color": COLORS["text"]},
        },
        "yAxis": {
            "type": "value",
            "name": "TEU",
            "splitLine": {"lineStyle": {"color": COLORS["grid"]}},
            "axisLabel": {"color": COLORS["text"]},
        },
        "series": [
            {
                "name": labels[partition],
                "type": "bar",
                "stack": "total",
                "barMaxWidth": 56,
                "data": values,
                "itemStyle": {"color": color_map[partition]},
            }
            for partition, values in series.items()
        ],
    }


def stuffing_option(values: dict[str, list[int]]) -> dict:
    categories = list(values)
    inside = [values[category][0] for category in categories]
    outside = [values[category][1] for category in categories]
    totals = [inside[index] + outside[index] for index in range(len(categories))]
    return {
        "animationDuration": 220,
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"bottom": 0, "data": ["Dalam", "Luar"], "textStyle": {"color": COLORS["text"]}},
        "grid": _base_grid(left=24, right=68, bottom=44, top=22),
        "xAxis": {
            "type": "value",
            "splitLine": {"lineStyle": {"color": COLORS["grid"]}},
            "axisLabel": {"color": COLORS["text"]},
        },
        "yAxis": {
            "type": "category",
            "data": categories,
            "axisTick": {"show": False},
            "axisLine": {"show": False},
            "axisLabel": {"color": COLORS["dark_green"]},
        },
        "series": [
            {
                "name": "Dalam",
                "type": "bar",
                "stack": "activity",
                "data": inside,
                "barWidth": 30,
                "itemStyle": {"color": COLORS["dark_green"], "borderRadius": [2, 0, 0, 2]},
                "label": {"show": True, "position": "inside", "color": "white"},
            },
            {
                "name": "Luar",
                "type": "bar",
                "stack": "activity",
                "data": outside,
                "barWidth": 30,
                "itemStyle": {"color": COLORS["sage"], "borderRadius": [0, 2, 2, 0]},
                "label": {"show": True, "position": "inside", "color": COLORS["dark_green"]},
            },
            {
                "name": "Total",
                "type": "bar",
                "data": totals,
                "barGap": "-100%",
                "itemStyle": {"color": "transparent"},
                "label": {"show": True, "position": "right", "fontWeight": 700, "color": COLORS["dark_green"]},
                "silent": True,
            },
        ],
    }
