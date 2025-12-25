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
INTENT_NEARBY = ["附近", "哪裡", "在哪"]

def is_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]', text))

def clean_html(text):
    if not text: return ""
    clean = re.sub(r'<[^>]+>', '', text)
    clean = clean.replace('&nbsp;', ' ').replace('&amp;', '&')
    return clean.strip()

def google_map_link(store_name, area="埔里"):
    query = f"{area} {store_name}"
    return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(query)}"

def load_food_data(file_path):
    food_db = {}
    if not os.path.exists(file_path):
        print("❌ 找不到 KML")
        return {}

    if zipfile.is_zipfile(file_path):
        with zipfile.ZipFile(file_path, 'r') as z:
            kml = z.read('doc.kml')
    else:
        with open(file_path, 'rb') as f:
            kml = f.read()

    root = etree.fromstring(kml, etree.XMLParser(recover=True))
    folders = root.xpath(".//*[local-name()='Folder']")

    if folders:
        for folder in folders:
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
                food_db[cat] = stores
    return food_db

FOOD_DATABASE = load_food_data("埔里吃什麼.kml")

for cat, stores in FOOD_DATABASE.items():
    jieba.add_word(cat)
    for s in stores:
        jieba.add_word(s["name"])

def category_quick_reply():
    return QuickReply(items=[
        QuickReplyItem(action=MessageAction(label=cat, text=cat))
        for cat in FOOD_DATABASE.keys()
    ])

def store_flex(store):
    # 確保名稱和描述絕對不會是 None 或空值
    name = store.get("name", "未知店名")
    desc = clean_html(store.get("description", "暫無介紹"))
    if not desc: desc = "埔里在地美食"

    return {
        "type": "bubble",
        "size": "micro",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": name,
                    "weight": "bold",
                    "size": "lg",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": desc,
                    "wrap": True,
                    "size": "xs",
                    "color": "#8c8c8c",
                    "maxLines": 3
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#4285F4",
                    "action": {
                        "type": "uri",
                        "label": "查看地圖",
                        "uri": google_map_link(name)
                    }
                }
            ]
        }
    }

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@app.route("/")
def index():
    return "Puli Food Bot is running!"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    raw = event.message.text.strip()
    tokens = jieba.lcut(raw) if is_chinese(raw) else raw.lower().split()

    found_category = None
    found_store = None

    if any(w in raw for w in ["你好", "嗨", "hello", "hi", "餓" ,"美食","food","hungry"]):
        reply = TextMessage(
            text="我是埔里美食小助手 🍜\n想吃哪一類？",
            quick_reply=category_quick_reply()
        )
        send(event, [reply])
        return

    # -------- 隨機推薦 --------
    if any(w in raw for w in INTENT_RANDOM):
        all_stores = [s for stores in FOOD_DATABASE.values() for s in stores]
        found_store = random.choice(all_stores)

    # -------- 再推薦 --------
    if "再" in raw and user_id in user_last_category:
        found_category = user_last_category[user_id]

    # -------- 分類搜尋 --------
    if not found_category:
        for cat in FOOD_DATABASE:
            if any(w in cat for w in tokens):
                found_category = cat
                user_last_category[user_id] = cat
                break

    # -------- 店名搜尋 --------
    if not found_store and not found_category:
        for stores in FOOD_DATABASE.values():
            for s in stores:
                if any(w in s["name"] for w in tokens if len(w) > 1):
                    found_store = s
                    break

    # -------- 回覆 --------
    if found_category:
        category_stores = FOOD_DATABASE.get(found_category, [])
        if not category_stores:
            send(event, [TextMessage(text=f"抱歉，{found_category} 目前沒有店家資料 😅")])
            return

        # 抽樣並建立 bubbles
        stores = random.sample(category_stores, min(5, len(category_stores)))
        bubbles = [store_flex(s) for s in stores]
        
        # 確保 FlexMessage 結構完整
        try:
            flex_msg = FlexMessage(
                alt_text=f"{found_category} 推薦清單",
                contents={"type": "carousel", "contents": bubbles}
            )
            send(event, [flex_msg])
        except Exception as e:
            print(f"Flex Error: {e}")
            send(event, [TextMessage(text="傳送圖卡時發生錯誤，請聯絡管理員。")])
        return

    if found_store:
        send(event, [FlexMessage(
            alt_text=found_store["name"],
            contents=store_flex(found_store)
        )])
        return

    send(event, [TextMessage(
        text="我找不到相關美食 😅\n可以試試下面分類",
        quick_reply=category_quick_reply()
    )])

def send(event, messages):
    with ApiClient(configuration) as api:
        MessagingApi(api).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=messages
            )
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))



