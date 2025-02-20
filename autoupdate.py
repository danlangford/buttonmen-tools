#!/usr/bin/env python3

import requests
from bs4 import BeautifulSoup
import json
import jsons
from dataclasses import dataclass


@dataclass
class RStat:
    name: str
    bset: str
    tl: bool
    rate: float
    count: int


def statslurp():
    soup = BeautifulSoup(requests.get(
        "http://stats.dev.buttonweavers.com/ui/stats/button_stats.html").text,
                         'html.parser')

    stat_rows = soup.find_all("tr")
    retrieved_button_stats = {}
    for s_r in stat_rows:
        tds = s_r.find_all(name="td", recursive=False)
        if len(tds) == 0:
            continue
        rstat = RStat(tds[0].string.strip(),
                      tds[1].string.strip(),
                      tds[2].string.strip() == "Y",
                      float(tds[3].string.strip()),
                      int(tds[4].string.strip()))
        retrieved_button_stats[rstat.name] = rstat

    captionRef = soup.find_all("caption")[0]
    caption = captionRef.string.strip()

    print(retrieved_button_stats)

    with open('public/buttonstats.json', 'w', encoding='utf-8') as f:
        json.dump({"data": jsons.dump(retrieved_button_stats), "message": caption},
                  f, ensure_ascii=False, indent=4)


if __name__ == '__main__':
    statslurp()
