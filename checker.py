import json
import os
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


URL = "https://sunrise-checker.com/izumo_down.html"
TARGET_DATES = {
    "2026-09-10": "9月10日",
    "2026-09-11": "9月11日",
    "2026-09-12": "9月12日",
}
STATE_FILE = Path("state.json")


def normalize(text):
    return (
        text.replace("〇", "○")
        .replace("　", " ")
        .replace("\n", " ")
        .strip()
    )


def date_in_text(text, date_key):
    year, month, day = date_key.split("-")
    text = normalize(text)

    patterns = [
        rf"{year}\s*[年/-]\s*0?{int(month)}\s*[月/-]\s*0?{int(day)}\s*日?",
        rf"0?{int(month)}\s*月\s*0?{int(day)}\s*日",
        rf"0?{int(month)}\s*/\s*0?{int(day)}",
        rf"0?{int(month)}\s*-\s*0?{int(day)}",
    ]

    return any(re.search(pattern, text) for pattern in patterns)


def find_status(text):
    text = normalize(text)

    for symbol in ("○", "△", "×"):
        if symbol in text:
            return symbol

    return ""


def read_seats(html):
    soup = BeautifulSoup(html, "html.parser")
    result = {}

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        header_cells = rows[0].find_all(["th", "td"])
        headers = [normalize(cell.get_text(" ", strip=True)) for cell in header_cells]

        single_index = next(
            (i for i, value in enumerate(headers) if "シングル" in value),
            None,
        )
        solo_index = next(
            (i for i, value in enumerate(headers) if "ソロ" in value),
            None,
        )

        if single_index is None or solo_index is None:
            for row in rows[:3]:
                cells = row.find_all(["th", "td"])
                candidate = [normalize(cell.get_text(" ", strip=True)) for cell in cells]

                single_index = next(
                    (i for i, value in enumerate(candidate) if "シングル" in value),
                    single_index,
                )
                solo_index = next(
                    (i for i, value in enumerate(candidate) if "ソロ" in value),
                    solo_index,
                )

        if single_index is None or solo_index is None:
            continue

        for row in rows[1:]:
            cells = row.find_all(["th", "td"])
            values = [normalize(cell.get_text(" ", strip=True)) for cell in cells]

            if not values:
                continue

            row_text = " ".join(values)

            for date_key, date_label in TARGET_DATES.items():
                if not date_in_text(row_text, date_key):
                    continue

                if single_index >= len(values) or solo_index >= len(values):
                    continue

                result[date_key] = {
                    "label": date_label,
                    "single": find_status(values[single_index]),
                    "solo": find_status(values[solo_index]),
                }

    if not result:
        raise RuntimeError("対象日またはシングル・ソロの表を読み取れませんでした")

    return result


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def send_mail(available):
    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ["MAIL_TO"]

    lines = [
        "サンライズ出雲の空席が見つかりました。",
        "",
    ]

    for item in available:
        lines.extend(
            [
                item["label"],
                f"シングル：{item['single'] or '記号なし'}",
                f"ソロ：{item['solo'] or '記号なし'}",
                "",
            ]
        )

    lines.extend(
        [
            "確認ページ：",
            URL,
        ]
    )

    message = EmailMessage()
    message["Subject"] = "サンライズ出雲の空席が見つかりました"
    message["From"] = user
    message["To"] = recipient
    message.set_content("\n".join(lines))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(message)


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(5_000)
        html = page.content()
        browser.close()

    if "エラー 再読み込みしてください" in html:
        raise RuntimeError("サイト側で空席表の読み込みエラーが発生しています")

    seats = read_seats(html)
    old_state = load_state()
    new_state = {}
    newly_available = []

    for date_key, item in seats.items():
        current = {
            "single": item["single"],
            "solo": item["solo"],
        }
        new_state[date_key] = current

        is_available = item["single"] == "○" or item["solo"] == "○"
        was_available = (
            old_state.get(date_key, {}).get("single") == "○"
            or old_state.get(date_key, {}).get("solo") == "○"
        )

        if is_available and not was_available:
            newly_available.append(item)

    save_state(new_state)

    if newly_available:
        send_mail(newly_available)
        print("空席を検出し、メールを送信しました")
    else:
        print("新しい空席はありません")


if __name__ == "__main__":
    main()
