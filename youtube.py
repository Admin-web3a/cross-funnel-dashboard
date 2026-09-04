"""Каталог роликов с YouTube: метаданные и просмотры по двум каналам.

Работает на обычном API-ключе — просмотры, названия и даты публикации это
публичные данные. Приватная аналитика (показы, CTR, удержание) требует OAuth
и доступа владельца канала, это отдельный этап.

Расход квоты на полный проход: около 40 единиц из суточных 10 000.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.googleapis.com/youtube/v3"

CHANNELS = [
    ("crypto_inside", "CryptoInside"),
    ("web3academy_pro", "Web3 Academy"),
]

# Ролик короче этого порога считаем Shorts: с них нельзя увести трафик на сайт,
# ссылку можно дать только на обычное видео. YouTube не отдаёт признак Shorts
# через API, поэтому ориентируемся на длительность.
SHORTS_MAX_SEC = 180


def _key():
    k = os.environ.get("YouTube_Data_API_v3") or os.environ.get("YT_API_KEY")
    if not k:
        raise RuntimeError(
            "Нет ключа YouTube. Ожидается YouTube_Data_API_v3 в окружении."
        )
    return k


def _get(endpoint, **params):
    params["key"] = _key()
    url = f"{API}/{endpoint}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"YouTube API {e.code} на {endpoint}: {body}") from None


def _duration_sec(iso):
    """PT1H2M3S -> секунды. Прямые эфиры отдают P0D, это ноль."""
    m = re.match(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _paged(endpoint, **params):
    token = None
    while True:
        if token:
            params["pageToken"] = token
        data = _get(endpoint, **params)
        for item in data.get("items", []):
            yield item
        token = data.get("nextPageToken")
        if not token:
            return


def fetch_catalog():
    """Все ролики обоих каналов: id -> метаданные и просмотры."""
    catalog = {}
    for handle, label in CHANNELS:
        ch = _get("channels", part="contentDetails", forHandle="@" + handle)
        items = ch.get("items") or []
        if not items:
            raise RuntimeError(f"Канал @{handle} не найден")
        uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        ids = [
            it["contentDetails"]["videoId"]
            for it in _paged("playlistItems", part="contentDetails",
                             playlistId=uploads, maxResults=50)
        ]

        # videos.list принимает до 50 id за вызов
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            data = _get("videos", part="snippet,statistics,contentDetails,liveStreamingDetails",
                        id=",".join(batch), maxResults=50)
            for v in data.get("items", []):
                sn, st = v["snippet"], v.get("statistics", {})
                dur = _duration_sec(v.get("contentDetails", {}).get("duration"))
                live = "liveStreamingDetails" in v
                catalog[v["id"]] = {
                    "title": sn.get("title", ""),
                    "channel": label,
                    "published": sn.get("publishedAt", "")[:10],
                    "views": int(st.get("viewCount") or 0),
                    "likes": int(st.get("likeCount") or 0),
                    "comments": int(st.get("commentCount") or 0),
                    "duration": dur,
                    "is_short": 0 < dur <= SHORTS_MAX_SEC and not live,
                    "format": "стрим" if live else ("shorts" if 0 < dur <= SHORTS_MAX_SEC else "видео"),
                }
    return catalog


if __name__ == "__main__":
    cat = fetch_catalog()
    longform = {k: v for k, v in cat.items() if not v["is_short"]}
    print(f"Всего роликов: {len(cat)}, из них не-Shorts: {len(longform)}")
    for vid, v in sorted(longform.items(), key=lambda x: -x[1]["views"])[:5]:
        print(f"   {v['published']}  {v['views']:>9,}  {v['title'][:56]}")
