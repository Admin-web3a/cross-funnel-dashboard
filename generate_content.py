#!/usr/bin/env python3
"""
Контент-дашборд: результаты по конкретным роликам и кластерам.

Сценарий использования — планёрка по контенту: открыли и видим, что выложили,
как каждый ролик раскрывается на дистанции и какие кластеры несут выручку.

Отдельная страница, а не блок в сквозной воронке: сквозная воронка режет данные
по СТАДИЯМ, а эта — по ЕДИНИЦЕ КОНТЕНТА. Это ортогональные оси, смешивать их
в одном экране неудобно.

Ключ склейки — YouTube video_id, который приезжает в utm_content.

Источники данных разделены по природе:
  • YouTube Data API — объективные метаданные: название, дата публикации,
    просмотры, длительность. Заполняется само, руками трогать нечего.
  • Справочник (Google Sheets или registry.csv) — экспертная классификация:
    кластер, функция контента, эксперт. Только то, что человек проставляет
    сам. В метке эта классификация НЕ хранится: метка после публикации
    неизменна, а классификация будет меняться, и переклассификация должна
    пересчитывать историю задним числом.

Пока только YouTube. Telegram размечается датой поста, а не идентификатором,
и в CRM по нему есть мусорные ссылки — до наведения порядка в метках он сюда
не попадает.

Просмотры накопительные за всё время жизни ролика: Data API не отдаёт разбивку
по дням. Поэтому фильтр по датам влияет на лиды и выручку, но не на просмотры.
Подневную статистику даст YouTube Analytics API на этапе 2, там нужен OAuth.
"""

import csv
import datetime
import io
import json
import os
import urllib.request

import generate_report as core
import youtube

REGISTRY_URL = os.environ.get("REGISTRY_URL", "").strip()
REGISTRY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry.csv")

CLUSTER_NAMES = {
    "beginner": "Новички в крипте",
    "cashflow": "Кешфлоу и DeFi",
    "capital": "Управление капиталом",
    "offtopic": "Вне тематики",
    "": "Не расклассифицировано",
}

# Функции контента с расшифровкой: без неё названия ничего не говорят тем,
# кто не сидел на разборе доски.
FUNCTIONS = {
    "discovery": ("Discovery", "Приводит новую аудиторию, которая нас ещё не знает. "
                               "Широкая тема, расчёт на поиск и рекомендации."),
    "authority": ("Authority", "Показывает экспертизу: объясняет сложное просто. "
                               "Строит доверие к эксперту, а не продаёт."),
    "belief_shift": ("Belief Shift", "Меняет убеждение, которое мешает купить. "
                                     "Например «крипта — это казино» → «это инструмент "
                                     "управления капиталом»."),
    "money_maker": ("Money Maker", "Ведёт к продаже напрямую: результат, кейсы, "
                                   "разбор оффера. Смотрит тёплая аудитория."),
    "event": ("Event", "Привязан к событию: запуск, вебинар, инфоповод. "
                       "Даёт всплеск и быстро выдыхается."),
    "": ("Не размечено", "Функция ещё не проставлена в справочнике."),
}
FUNCTION_NAMES = {k: v[0] for k, v in FUNCTIONS.items()}
FUNCTION_DESC = {k: v[1] for k, v in FUNCTIONS.items()}


def load_registry():
    """Экспертная классификация роликов. Метаданные сюда не входят."""
    if REGISTRY_URL:
        with urllib.request.urlopen(REGISTRY_URL, timeout=30) as r:
            text = r.read().decode("utf-8")
        source = "Google Sheets"
    else:
        text = open(REGISTRY_FILE, encoding="utf-8").read()
        source = "registry.csv"
    reg = {}
    for row in csv.DictReader(io.StringIO(text)):
        cid = (row.get("content_id") or "").strip()
        if cid:
            reg[cid] = {k: (row.get(k) or "").strip()
                        for k in ("cluster", "function", "expert", "confirmed", "note")}
    return reg, source


def build():
    journeys, _, meta = core.build()
    reg, source = load_registry()

    try:
        catalog = youtube.fetch_catalog()
        yt_error = ""
    except Exception as e:
        catalog, yt_error = {}, str(e)

    # Момент, с которого воронка вообще начала собирать лиды. Всё, что вышло
    # раньше, получило ссылку в описании задним числом, и для таких роликов
    # отсчёт недель от публикации смысла не имеет.
    funnel_start = min((j["ts"] for j in journeys), default=0)

    # Только YouTube-органика: платный трафик живёт в сквозной воронке,
    # Telegram ждёт наведения порядка в метках.
    import re as _re
    _YT_ID = _re.compile(r'^[A-Za-z0-9_-]{11}$')

    items = {}
    for j in journeys:
        cnt = j.get("cnt") or ""
        if j["src"] == "yt" and j["tt"] == "organic" and _YT_ID.match(cnt):
            items.setdefault(cnt, []).append(j)

    videos = []
    for vid, group in items.items():
        r = reg.get(vid, {})
        c = catalog.get(vid, {})
        paid = [g for g in group if g["st"] >= core.STAGE_PAID]
        published = c.get("published", "")
        videos.append({
            "id": vid,
            "title": c.get("title") or "",
            "channel": c.get("channel") or "",
            "published": published,
            "views": c.get("views") or 0,
            "fmt": c.get("format") or "",
            "inCatalog": vid in catalog,
            # Ролик вышел уже при работающей воронке: только для таких
            # когорта «недель от публикации» отражает реальную дистанцию.
            "postFunnel": bool(published) and
                          datetime.date.fromisoformat(published) >=
                          datetime.date.fromtimestamp(funnel_start),
            "cluster": r.get("cluster") or "",
            "func": r.get("function") or "",
            "expert": r.get("expert") or "",
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
            # Все агрегаты пересчитываются из этого списка при фильтре по датам,
            # поэтому здесь должно лежать всё, что показывается в таблицах.
            "leads": [{"ts": g["ts"], "st": g["st"], "op": g["op"],
                       "rev": g["rev"], "twr": g["twr"]} for g in group],
        })
    videos.sort(key=lambda v: -v["n"])

    # Ролики, вышедшие уже при работающей воронке и не давшие ни одного лида.
    # Именно они и есть сигнал: ролик есть, ссылка должна была быть, лидов нет.
    # Старый каталог сюда не берём — там отсутствие лидов ожидаемо.
    start_date = datetime.date.fromtimestamp(funnel_start).isoformat() if funnel_start else ""
    silent = [{"id": vid, "title": c["title"], "published": c["published"],
               "views": c["views"], "channel": c["channel"]}
              for vid, c in catalog.items()
              if vid not in items and not c["is_short"] and c["published"] >= start_date]
    silent.sort(key=lambda s: s["published"], reverse=True)

    return videos, silent, {
        "registry_source": source,
        "registry_size": len(reg),
        "unclassified": len([v for v in videos if not v["cluster"]]),
        "unconfirmed": len([v for v in videos if reg.get(v["id"], {}).get("confirmed") != "yes"]),
        "catalogSize": len(catalog),
        "ytError": yt_error,
        "funnelStart": start_date,
        "preFunnel": len([v for v in videos if not v["postFunnel"]]),
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
            .replace("__FUNCDESC__", dump(FUNCTION_DESC))
            .replace("__META__", dump(meta))
            .replace("__UPDATED__", updated))


CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".data-cache.json")

if __name__ == "__main__":
    import sys
    # --cached переиспользует прошлую выгрузку: полный проход по amoCRM идёт
    # несколько минут, а при правке вёрстки данные не меняются.
    if "--cached" in sys.argv and os.path.exists(CACHE):
        v, s, m = json.load(open(CACHE, encoding="utf-8"))
        print("Данные из кэша, CRM не опрашивалась")
    else:
        v, s, m = build()
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump([v, s, m], f, ensure_ascii=False)
    docs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    os.makedirs(docs, exist_ok=True)
    out = os.path.join(docs, "content.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(v, s, m))
    print(f"OK: {len(v)} роликов с лидами, {len(s)} без лидов после {m['funnelStart']}, "
          f"каталог {m['catalogSize']}, справочник {m['registry_size']} -> {out}")
    if m["ytError"]:
        print(f"ВНИМАНИЕ, YouTube недоступен: {m['ytError']}")
