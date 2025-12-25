import os
import zipfile
import random
import re
import urllib.parse
import jieba
from lxml import etree
from flask import Flask, request, abort

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage,
    QuickReply, QuickReplyItem, MessageAction,
    FlexMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

user_last_category = {}

INTENT_RANDOM = ["隨便", "不知道", "吃什麼", "推薦", "幫我選"]

# ---------- 工具 ----------

def is_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]', text))

def clean_html(text):
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    return text.replace('&nbsp;', ' ').replace('&amp;', '&').strip()

def google_map_link(name, area="埔里"):
    q = f"{area} {name}"
    return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(q)}"

# ---------- 讀 KML ----------

def load_food_data(path):
    food = {}
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            kml = z.read("doc.kml")
    else:
        with open(path, "rb") as f:
            kml = f.read()

    root = etree.fromstring(kml, etree.XMLParser(recover=True))
    for folder in root.xpath(".//*[local-name()='Folder']"):
        cat = folder.xpath("./*[local-name()='name']/text()")
        cat = cat[0] if cat else "其他"
        stores = []
        for p in folder.xpath(".//*[local-name()='Placemark']"):
            name = p.xpath("./*[local-name()='name']/text()")
            desc = p.xpath("./*[local-name()='description']/text()")
            if name:
                stores.append({
                    "name": name[0],
                    "description": desc[0] if desc else "埔里在地美食"
                })
        if stores:
            food[cat] = stores
    return food

FOOD_DATABASE = load_food_data("埔里吃什麼.kml")

for c, ss in FOOD_DATABASE.items():
    jieba.add_word(c)
    for s in ss:
        jieba.add_word(s["name"])

# ---------- UI ----------

def category_quick_reply():
    return QuickReply(items=[
        QuickReplyItem(action=MessageAction(label=c, text=c))
        for c in FOOD_DATABASE
    ])

def store_bubble(store):
    return {
        "type": "bubble",
        "size": "micro",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": store["name"], "weight": "bold", "wrap": True},
                {"type": "text", "text": clean_html(store["description"]), "size": "xs", "wrap": True}
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [{
                "type": "button",
                "style": "primary",
                "action": {
                    "type": "uri",
                    "label": "查看地圖",
                    "uri": google_map_link(store["name"])
                }
            }]
        }
    }

# ---------- Flask ----------

@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    sig = request.headers.get("X-Line-Signature")
    try:
        handler.handle(body, sig)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@app.route("/")
def index():
    return "Puli Food Bot Running"

# ---------- LINE 邏輯 ----------

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()
    user = event.source.user_id
    tokens = jieba.lcut(text) if is_chinese(text) else text.lower().split()

    # 打招呼
    if any(w in text for w in ["你好", "嗨", "餓", "美食"]):
        reply(event, TextMessage(
            text="我是埔里美食小助手 🍜\n你想吃哪一類？",
            quick_reply=category_quick_reply()
        ))
        return

    # 隨機
    if any(w in text for w in INTENT_RANDOM):
        s = random.choice([x for v in FOOD_DATABASE.values() for x in v])
        reply(event, FlexMessage(
            alt_text=s["name"],
            contents=store_bubble(s)
        ))
        return

    # 分類
    for cat, stores in FOOD_DATABASE.items():
        if any(t in cat for t in tokens):
            user_last_category[user] = cat
            bubbles = [store_bubble(s) for s in random.sample(stores, min(5, len(stores)))]
            reply(event, FlexMessage(
                alt_text=f"{cat} 推薦",
                contents={"type": "carousel", "contents": bubbles}
            ))
            return

    reply(event, TextMessage(
        text="找不到相關美食 😅\n請選分類",
        quick_reply=category_quick_reply()
    ))

# ---------- 安全回覆 ----------

def reply(event, message):
    with ApiClient(configuration) as api:
        MessagingApi(api).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[message]
            )
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
