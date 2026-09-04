#!/usr/bin/env python3
"""
Сквозная воронка: трафик -> трипвайер -> диагностика -> отдел продаж -> выручка.

Трипвайер-воронка (трёхчастная статья + покупка AFK) реализована двумя
лендингами — Money-Dick и PetRock. Трафик в неё заливается из разных каналов:
органика с YouTube и Telegram, платный трафик Meta. Поэтому лид трипвайер-воронки
НЕ является «контентным лидом»: канал — это отдельное измерение, а не свойство
воронки. Вклад контент-завода измеряется фильтром по органическим источникам.

Склеивает четыре воронки amoCRM по contact_id и проверяет причинно-следственный
порядок: сделка ОП засчитывается трафику, только если она создана ПОЗЖЕ лида
трипвайер-воронки. Обратный порядок означает, что контакт уже был в базе.

Вход в ОП НЕ является продолжением трипвайер-воронки: большинство контактов
попадают туда параллельным путём, минуя и дочитывание статьи, и диагностику.
Поэтому линейная воронка и пути в ОП показаны раздельно.

Единицей счёта является КОНТАКТ, а не сделка — в отличие от md-dashboard и
petrock-dashboard, которые считают лиды. Расхождение в несколько процентов
между дашбордами объясняется именно этим.

Данные встраиваются в HTML как JSON, вся фильтрация — на клиенте.
"""

import datetime
import json
import os
import re
import urllib.parse
import urllib.request

def _load_env():
    """Подхватить .env, если переменных нет в окружении.

    В GitHub Actions всё приходит из секретов, а локально удобнее не помнить,
    какой файл сорсить перед запуском.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, ".env"),
        os.path.join(here, "..", ".env"),
        os.path.join(here, "..", "Docs", "Infrastructure", "motivation-calc", ".env"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_env()
TOKEN = os.environ["AMO_TOKEN"]
DOMAIN = os.environ.get("AMO_DOMAIN", "simmihur.amocrm.ru")

PIPE_MD = 11095182
PIPE_PR = 11218594
PIPE_DIAG = 11121294
PIPE_OP = 9826550

WON_OP = {142, 78184766}
LOST = 143

HISTORY_FROM = int(datetime.datetime(2026, 1, 1).timestamp())

MD_STAGES = [
    (87129254, "Лид создан"), (87129258, "Часть 1 открыта"), (87129262, "Часть 1 прочитана"),
    (87315330, "Часть 2 открыта"), (87315334, "Часть 2 прочитана"), (87315338, "Часть 3 открыта"),
    (87315342, "Часть 3 прочитана"), (87315346, "Увидел оффер"), (87315350, "Тариф выбран"),
    (87315354, "Checkout открыт"), (87315358, "Данные checkout отправлены"),
    (87315362, "Payment intent created"), (87315366, "Платёжная форма готова"),
    (87315370, "Оплата не прошла"), (87315374, "Оплачено"), (142, "Успешно реализовано"),
]
PR_STAGES = [
    (88017134, "Лид создан"), (88019002, "Часть 1 открыта"), (88019006, "Часть 1 прочитана"),
    (88019010, "Часть 2 открыта"), (88019014, "Часть 2 прочитана"), (88019018, "Часть 3 открыта"),
    (88019022, "Часть 3 прочитана"), (88019026, "Увидел оффер"), (88019030, "Тариф выбран"),
    (88017138, "Checkout открыт"), (88017142, "Данные checkout отправлены"),
    (88017146, "Payment intent created"), (88017150, "Платёжная форма готова"),
    (88017154, "Оплата не прошла"), (88019034, "Оплачено"), (142, "Успешно реализовано"),
]
MD_IDX = {s: i for i, (s, _) in enumerate(MD_STAGES)}
PR_IDX = {s: i for i, (s, _) in enumerate(PR_STAGES)}
CONTENT_STAGE_NAMES = [n for _, n in MD_STAGES]

STAGE_PAID = 14

# Воронка «Диагностика» после расширения. Линейные шаги и ответвления разделены:
# «клиент не пришёл» — это не шаг вперёд, а исход после согласования слота.
DIAG_LINEAR = [
    (87316698, "Новая заявка"), (88331358, "Взят в работу"), (88331366, "Контакт установлен"),
    (88331370, "Слот согласован"), (88331374, "Диагностика проведена"),
    (88331382, "Выставлен счёт"), (142, "Успешно реализовано"),
]
DIAG_IDX = {s: i for i, (s, _) in enumerate(DIAG_LINEAR)}
DIAG_NAMES = [n for _, n in DIAG_LINEAR]
DIAG_NO_SHOW = 88331378
DIAG_BRANCH = {88331362: "НДЗ", DIAG_NO_SHOW: "Клиент не пришёл",
               88331386: "Отложенный спрос", 143: "Закрыто и не реализовано"}
DIAG_SLOT_IDX = 3  # «Слот согласован» — знаменатель для show-rate

UTM_MD = {"source": 1323539, "medium": 1323541, "campaign": 1323543, "content": 1323545, "term": 1323547}
UTM_PR = {"source": 1323905, "medium": 1323907, "campaign": 1323909, "content": 1323911, "term": 1323913}

# utm_medium занят под идентификатор подрядчика, поэтому платность трафика
# определить машинно нельзя — только по списку источников. Это временный костыль
# до введения единого UTM-справочника.
PAID_SOURCES = {"fb", "vk", "google", "yandex", "mytarget", "tgads"}


def traffic_type(src):
    if not src:
        return "unknown"
    return "paid" if src.lower() in PAID_SOURCES else "organic"


def amo_get(path, params):
    url = f"https://{DOMAIN}{path}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return {}
        raise
    # amoCRM отдаёт пустое тело вместо 204, когда в выборке не осталось записей.
    return json.loads(body) if body.strip() else {}


def fetch_leads(pipeline_id, created_from=None):
    out, page = [], 1
    while True:
        params = {"page": page, "limit": 250, "filter[pipeline_id]": pipeline_id, "with": "contacts"}
        if created_from:
            params["filter[created_at][from]"] = created_from
        data = amo_get("/api/v4/leads", params)
        batch = (data.get("_embedded") or {}).get("leads", [])
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 250:
            break
        page += 1
    return out


def cf(lead, field_id):
    for f in lead.get("custom_fields_values") or []:
        if f["field_id"] == field_id:
            vals = f.get("values") or []
            if vals:
                return str(vals[0].get("value") or "").strip()
    return ""


def contact_ids(lead):
    return [c["id"] for c in (lead.get("_embedded") or {}).get("contacts", [])]


def tripwire_payment(lead):
    """Сумма и ценовой вариант оплаты трипвайера.

    Штатное место для суммы — поле price сделки, как в основной воронке ОП.
    Сейчас оно пустое у всех сделок, а фактическая сумма лежит только в тексте
    примечания `Moneydick event: paid` (там же price_variant: цена зависит от
    источника трафика). Поэтому price читается первым, а примечание работает
    запасным вариантом — когда интеграция начнёт заполнять price, поведение
    не изменится и запрос примечаний отключать не придётся.
    """
    if lead.get("price"):
        return float(lead["price"]), ""
    data = amo_get(f"/api/v4/leads/{lead['id']}/notes", {"limit": 100})
    amount, variant = 0.0, ""
    for note in (data.get("_embedded") or {}).get("notes", []):
        text = ((note.get("params") or {}).get("text") or "")
        if "event: paid" not in text:
            continue
        m = re.search(r"amount:\s*([\d.]+)", text)
        v = re.search(r"price_variant:\s*(\S+)", text)
        if m and float(m.group(1)) >= amount:
            amount = float(m.group(1))
            variant = v.group(1) if v else ""
    return amount, variant


def build():
    md = fetch_leads(PIPE_MD)
    pr = fetch_leads(PIPE_PR)
    diag = fetch_leads(PIPE_DIAG)
    op = fetch_leads(PIPE_OP, HISTORY_FROM)

    content = {}

    def collect(leads, idx_map, utm_map, funnel_name):
        for l in leads:
            if l.get("status_id") == LOST or l.get("status_id") not in idx_map:
                continue
            stage = idx_map[l["status_id"]]
            created = l.get("created_at") or 0
            amount, variant = tripwire_payment(l) if stage >= STAGE_PAID else (0.0, "")
            for cid in contact_ids(l):
                rec = content.get(cid)
                if rec is None:
                    content[cid] = {
                        "first_ts": created, "stage": stage, "funnel": funnel_name,
                        "src": cf(l, utm_map["source"]), "cnt": cf(l, utm_map["content"]),
                        "cmp": cf(l, utm_map["campaign"]), "term": cf(l, utm_map["term"]),
                        "tw_rev": amount, "pv": variant,
                    }
                    continue
                rec["first_ts"] = min(rec["first_ts"], created)
                rec["stage"] = max(rec["stage"], stage)
                for key, fid in (("src", utm_map["source"]), ("cnt", utm_map["content"]),
                                 ("cmp", utm_map["campaign"]), ("term", utm_map["term"])):
                    if not rec[key]:
                        rec[key] = cf(l, fid)
                rec["tw_rev"] += amount
                if variant and not rec.get("pv"):
                    rec["pv"] = variant

    collect(md, MD_IDX, UTM_MD, "Money-Dick")
    collect(pr, PR_IDX, UTM_PR, "PetRock")

    # ── Диагностика: глубина по линейной шкале + отдельный флаг no-show
    diag_by_contact = {}
    for l in diag:
        sid = l.get("status_id")
        depth = DIAG_IDX.get(sid, -1)
        no_show = 1 if sid == DIAG_NO_SHOW else 0
        if no_show:
            depth = DIAG_SLOT_IDX  # слот был согласован, но клиент не явился
        branch = DIAG_BRANCH.get(sid, "")
        ts = l.get("created_at") or 0
        for cid in contact_ids(l):
            rec = diag_by_contact.get(cid)
            if rec is None:
                diag_by_contact[cid] = {"depth": depth, "ns": no_show, "br": branch, "ts": ts}
            else:
                rec["depth"] = max(rec["depth"], depth)
                rec["ns"] = max(rec["ns"], no_show)
                rec["ts"] = min(rec["ts"], ts)
                if branch and not rec["br"]:
                    rec["br"] = branch

    op_by_contact = {}
    for l in op:
        for cid in contact_ids(l):
            op_by_contact.setdefault(cid, []).append(l)

    journeys = []
    for cid, c in content.items():
        op_leads = op_by_contact.get(cid, [])
        after = [l for l in op_leads if (l.get("created_at") or 0) > c["first_ts"]]
        before = [l for l in op_leads if (l.get("created_at") or 0) <= c["first_ts"]]
        won_after = [l for l in after if l.get("status_id") in WON_OP]

        d = diag_by_contact.get(cid)
        journeys.append({
            "ts": c["first_ts"],
            "st": c["stage"],
            "fn": c["funnel"],
            "src": c["src"] or "(нет метки)",
            "tt": traffic_type(c["src"]),
            "trm": c["term"] or "(нет метки)",
            "cnt": c["cnt"] or "(нет метки)",
            "cmp": c["cmp"] or "(нет метки)",
            "twr": c["tw_rev"],
            "pv": c.get("pv") or "",
            "dg": 1 if d else 0,
            "dgd": d["depth"] if d else -1,
            "dgn": d["ns"] if d else 0,
            "dgb": d["br"] if d else "",
            "op": len(after),
            "opb": len(before),
            "won": len(won_after),
            "rev": sum(l.get("price") or 0 for l in won_after),
            "revb": sum(l.get("price") or 0 for l in before if l.get("status_id") in WON_OP),
        })

    # Диагностика целиком, включая заявки без контентного лида
    diag_all = []
    for cid, d in diag_by_contact.items():
        diag_all.append({"depth": d["depth"], "ns": d["ns"], "br": d["br"],
                         "ts": d["ts"], "fromContent": 1 if cid in content else 0})

    meta = {
        "md_total": len(md), "pr_total": len(pr),
        "diag_total": len(diag), "op_total": len(op),
    }
    return journeys, diag_all, meta


def render(journeys, diag_all, meta):
    updated = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=3))
    ).strftime("%d.%m.%Y %H:%M МСК")
    here = os.path.dirname(os.path.abspath(__file__))
    tpl = open(os.path.join(here, "template.html"), encoding="utf-8").read()
    dump = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    return (tpl
            .replace("__DATA__", dump(journeys))
            .replace("__DIAG__", dump(diag_all))
            .replace("__CSTAGES__", dump(CONTENT_STAGE_NAMES))
            .replace("__DSTAGES__", dump(DIAG_NAMES))
            .replace("__META__", dump(meta))
            .replace("__UPDATED__", updated))


CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".report-cache.json")

if __name__ == "__main__":
    import sys
    # --cached переиспользует прошлую выгрузку: полный проход по amoCRM идёт
    # несколько минут, а при правке вёрстки данные не меняются.
    if "--cached" in sys.argv and os.path.exists(CACHE):
        j, d, m = json.load(open(CACHE, encoding="utf-8"))
        print("Данные из кэша, CRM не опрашивалась")
    else:
        j, d, m = build()
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump([j, d, m], f, ensure_ascii=False)
    html = render(j, d, m)
    docs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    os.makedirs(docs, exist_ok=True)
    out = os.path.join(docs, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"OK: {len(j)} контактов воронки, {len(d)} в диагностике -> {out}")
    print(f"    Money-Dick={m['md_total']} PetRock={m['pr_total']} "
          f"Диагностика={m['diag_total']} ОП={m['op_total']}")
