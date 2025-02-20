#!/usr/bin/env python3

import requests
from bs4 import BeautifulSoup
import json
import jsons
from dataclasses import dataclass
from datetime import datetime
import os


@dataclass
class RStat:
    name: str
    bset: str
    tl: bool
    rate: float
    count: int


def buttonstats():
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

    with open('public/buttonstats.json', 'w', encoding='utf-8') as f:
        json.dump({"data": jsons.dump(retrieved_button_stats), "message": caption},
                  f, ensure_ascii=False, indent=4)


def fetch_button_data(site):
    session = requests.Session()
    login_response = session.post(
        f"https://{site}.buttonweavers.com/api/responder",
        json={"type": "login", "username": "bagels", "password": os.getenv('BUTTONWEAVERS_PW'),
              "doStayLoggedIn": False},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    if login_response.json()['status'] == 'ok':
        print("Login successful")

        response = session.post(
            f"https://{site}.buttonweavers.com/api/responder",
            json={"type": "loadButtonData", "automatedApiCall": False},
            headers={"Content-Type": "application/x-www-form-urlencoded"}).json()
        now = datetime.now()
        response['message'] = f"{site.upper()} {response['message']} {now.strftime("%Y-%m-%d")}"
        return response

    else:
        print("Login failed")
        return None


def buttondata():
    response = fetch_button_data("www")
    with open('public/buttondata.json', 'w', encoding='utf-8') as f:
        json.dump(response, f, ensure_ascii=False, indent=4)


def buttonunimpl():
    response = fetch_button_data("staging")
    with open('public/buttonunimpl.json', 'w', encoding='utf-8') as f:
        json.dump(response, f, ensure_ascii=False, indent=4)


if __name__ == '__main__':
    print("Updating prod button data…")
    buttondata()
    print("Updating staging button data…")
    buttonunimpl()
    print("Updating button stats…")
    buttonstats()
