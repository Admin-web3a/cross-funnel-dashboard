#!/usr/bin/env python3
"""
Контент-дашборд: результаты по конкретным роликам и кластерам.

Сценарий использования — понедельничная планёрка по контенту: открыли и видим,
что выложили, как каждый ролик раскрывается на дистанции и какие кластеры несут
выручку.

Отдельная страница, а не блок в сквозной воронке: сквозная воронка режет данные
по СТАДИЯМ, а эта — по ЕДИНИЦЕ КОНТЕНТА. Это ортогональные оси, смешивать их
в одном экране неудобно.

Ключ склейки — YouTube video_id, который приезжает в utm_content. Кластер и
функция контента НЕ хранятся в метке: они меняются, а метка неизменна после
публикации ролика. Соответствие video_id -> кластер лежит в справочнике,
поэтому переклассификация пересчитывает всю историю задним числом.

Справочник читается из Google Sheets (переменная REGISTRY_URL с CSV-экспортом
опубликованной таблицы) либо из локального registry.csv.

Этап 1: без просмотров. Метрика «выручка на 1000 просмотров» требует YouTube
Analytics API и появится на этапе 2 — колонки под неё уже размечены.
"""

import csv
import datetime
import io
import json
import os
import urllib.request

import generate_report as core

REGISTRY_URL = os.environ.get("REGISTRY_URL", "").strip()
REGISTRY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry.csv")

CLUSTER_NAMES = {
    "beginner": "Новички в крипте",
    "cashflow": "Кешфлоу и DeFi",
    "capital": "Управление капиталом",
    "offtopic": "Вне тематики",
    "": "Не расклассифицировано",
}
FUNCTION_NAMES = {
    "discovery": "Discovery",
    "authority": "Authority",
    "belief_shift": "Belief Shift",
    "money_maker": "Money Maker",
    "event": "Event",
    "": "Не размечено",
}


def load_registry():
    if REGISTRY_URL:
        with urllib.request.urlopen(REGISTRY_URL, timeout=30) as r:
            text = r.read().decode("utf-8")
        source = "Google Sheets"
    else:
        text = open(REGISTRY_FILE, encoding="utf-8").read()
        source = "registry.csv"
    rows = list(csv.DictReader(io.StringIO(text)))
    reg = {}
    for row in rows:
        cid = (row.get("content_id") or "").strip()
        if not cid:
            continue
        reg[cid] = {k: (row.get(k) or "").strip() for k in
                    ("title", "platform", "channel", "cluster", "function",
                     "expert", "format", "confirmed")}
    return reg, source


def build():
    journeys, _, meta = core.build()
    reg, source = load_registry()

    # Вся органика: YouTube, Telegram-канал, блог. Платный трафик — в сквозной
    # воронке, здесь он только зашумил бы сравнение единиц контента.
    items = {}
    for j in journeys:
        if j["tt"] != "organic":
            continue
        items.setdefault(j["cnt"], []).append(j)

    videos = []
    for vid, group in items.items():
        r = reg.get(vid, {})
        paid = [g for g in group if g["st"] >= core.STAGE_PAID]
        videos.append({
            "id": vid,
            "title": r.get("title") or "",
            "platform": r.get("platform") or "",
            "src": sorted({g["src"] for g in group})[0],
            "channel": r.get("channel") or "",
            "cluster": r.get("cluster") or "",
            "func": r.get("function") or "",
            "expert": r.get("expert") or "",
            "fmt": r.get("format") or "",
            "known": vid in reg,
            "n": len(group),
            "read": len([g for g in group if g["st"] >= 6]),
            "checkout": len([g for g in group if g["st"] >= 9]),
            "tw": len(paid),
            "twRev": sum(g["twr"] for g in paid),
            "op": len([g for g in group if g["op"] > 0]),
            "won": len([g for g in group if g["won"] > 0]),
            "rev": sum(g["rev"] for g in group),
            "first": min(g["ts"] for g in group),
            "leads": [{"ts": g["ts"], "st": g["st"], "op": g["op"], "rev": g["rev"]} for g in group],
        })
    videos.sort(key=lambda v: -v["n"])

    # Ролики из справочника, по которым ещё не пришло ни одного лида
    silent = [{"id": vid, "title": r["title"], "cluster": r["cluster"]}
              for vid, r in reg.items()
              if vid not in items and r.get("format") != "placement"]

    return videos, silent, {
        "registry_source": source,
        "registry_size": len(reg),
        "unclassified": len([v for v in videos if not v["cluster"]]),
        "unconfirmed": len([vid for vid, r in reg.items() if r.get("confirmed") != "yes"]),
        **meta,
    }


def render(videos, silent, meta):
    here = os.path.dirname(os.path.abspath(__file__))
    tpl = open(os.path.join(here, "content_template.html"), encoding="utf-8").read()
    updated = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=3))
    ).strftime("%d.%m.%Y %H:%M МСК")
    dump = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    return (tpl
            .replace("__VIDEOS__", dump(videos))
            .replace("__SILENT__", dump(silent))
            .replace("__CLUSTERS__", dump(CLUSTER_NAMES))
            .replace("__FUNCTIONS__", dump(FUNCTION_NAMES))
            .replace("__META__", dump(meta))
            .replace("__UPDATED__", updated))


if __name__ == "__main__":
    v, s, m = build()
    docs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    os.makedirs(docs, exist_ok=True)
    out = os.path.join(docs, "content.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(v, s, m))
    print(f"OK: {len(v)} роликов с трафиком, {len(s)} без лидов, "
          f"справочник из {m['registry_source']} ({m['registry_size']}) -> {out}")
