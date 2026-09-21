#!/usr/bin/env python3

import json
from dataclasses import dataclass
from datetime import datetime
import os
import re
from pathlib import Path


OPERATION_ALLOWLISTS = {
    "operation_looking_glass_oz_button_name": 60.0,
}
CAPABILITY_NAMES = (
    "bmaibagels_supported_button_name",
    "bmaibagels_unsupported_button_name",
    "bmaibagels_unsupported_button_set",
    "bmaibagels_supported_die_features",
)


@dataclass
class RStat:
    name: str
    bset: str
    tl: bool
    rate: float
    count: int


def buttonstats():
    import jsons
    import requests
    from bs4 import BeautifulSoup

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
    import requests

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


def javascript_array(html, name):
    match = re.search(rf"\b{re.escape(name)}\s*=\s*(\[[\s\S]*?\])", html)
    if not match:
        raise ValueError(f"Could not find JavaScript array {name}")
    return match, json.loads(match.group(1))


def update_operation_allowlists(public_directory="public"):
    """Add currently qualifying buttons without removing earlier qualifiers."""
    public_directory = Path(public_directory)
    filter_path = public_directory / "ButtonFilter.html"
    with filter_path.open(encoding="utf-8") as f:
        html = f.read()
    with (public_directory / "buttondata.json").open(encoding="utf-8") as f:
        buttons = json.load(f)["data"]
    with (public_directory / "buttonstats.json").open(encoding="utf-8") as f:
        stats = json.load(f)["data"]

    capabilities = {
        name: set(javascript_array(html, name)[1])
        for name in CAPABILITY_NAMES
    }
    compatible = set()
    for button in buttons:
        name = button["buttonName"]
        features = set(button["dieSkills"] + button["dieTypes"])
        if (name in capabilities["bmaibagels_supported_button_name"] or (
                name not in capabilities["bmaibagels_unsupported_button_name"]
                and button["buttonSet"] not in
                capabilities["bmaibagels_unsupported_button_set"]
                and features <=
                capabilities["bmaibagels_supported_die_features"])):
            compatible.add(name)

    for array_name, maximum_rate in OPERATION_ALLOWLISTS.items():
        match, existing = javascript_array(html, array_name)
        allowed = set(existing)
        allowed.update(
            name for name in compatible
            if name in stats and float(stats[name]["rate"]) < maximum_rate)
        replacement = (
            f"{array_name} = " +
            json.dumps(sorted(allowed, key=str.casefold), ensure_ascii=False,
                       indent=16))
        html = html[:match.start()] + replacement + html[match.end():]

    with filter_path.open("w", encoding="utf-8") as f:
        f.write(html)


if __name__ == '__main__':
    print("Preserving currently eligible operation buttons…")
    update_operation_allowlists()
    print("Updating prod button data…")
    buttondata()
    print("Updating staging button data…")
    buttonunimpl()
    print("Updating button stats…")
    buttonstats()
    print("Adding newly eligible operation buttons…")
    update_operation_allowlists()
