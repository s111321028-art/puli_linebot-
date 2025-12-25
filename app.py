import os
import zipfile
import random
import re
import urllib.parse
import jieba
from lxml import etree
from flask import Flask, request, abort

# LINE SDK v3 基礎匯入
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, 
    TextMessage, FlexMessage, QuickReply, QuickReplyItem, MessageAction
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

# ================= 基本設定 =================
app = Flask(__name__)

# 從環境變數讀取 Token (Render 後台設定)
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 使用者記憶：記錄上次查詢的分類
user_last_category = {}
INTENT_RANDOM = ["隨便", "不知道", "吃什麼", "推薦", "幫我選"]

# ================= 工具函式 =================
def is_chinese(text):
    """判斷字串是否包含中文字元"""
    return bool(re.search(r'[\u4e00-\u9fff]', text))

def clean_html(text):
    """移除 HTML 標籤並清理特殊字元，避免 Flex 報錯"""
    if not text: return ""
    clean = re.sub(r'<[^>]+>', '', text)
    clean = clean.replace(' ', ' ').replace('&', '&')
    return clean.strip()

def google_map_link(store_name, area="埔里"):
    """產生標準 Google 地圖搜尋連結"""
    query = f"{area} {store_name}"
    return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(query)}"

# ================= 載入 KML 資料 =================
def load_food_data(file_path):
    food_db = {}
    if not os.path.exists(file_path):
        print(f"❌ 找不到 KML: {file_path}")
        return {}
    try:
        if zipfile.is_zipfile(file_path):
            with zipfile.ZipFile(file_path, 'r') as z:
                kml_content = z.read('doc.kml')
        else:
            with open(file_path, 'rb') as f:
                kml_content = f.read()

        root = etree.fromstring(kml_content, etree.XMLParser(recover=True))
        folders = root.xpath(".//*[local-name()='Folder']")

        if folders:
            for folder in folders:
                cat_name_list = folder.xpath("./*[local-name()='name']/text()")
                cat = cat_name_list[0] if cat_name_list else "其他"
                stores = []
                for p in folder.xpath(".//*[local-name()='Placemark']"):
                    name = p.xpath("./*[local-name()='name']/text()")
                    desc = p.xpath("./*[local-name()='description']/text()")
                    if name:
                        stores.append({
                            "name": str(name[0]),
                            "description": str(desc[0]) if desc else "埔里在地美食，歡迎品嚐！"
                        })
                if stores:
                    food_db[cat] = stores
        return food_db
    except Exception as e:
        print(f"❌ 解析失敗: {e}")
        return {}

# 預載入資料
FOOD_DATABASE = load_food_data("埔里吃什麼.kml")

# 啟動時更新 jieba 詞庫
if FOOD_DATABASE:
    for cat, stores in FOOD_DATABASE.items():
        jieba.add_word(cat)
        for s in stores:
            jieba.add_word(s["name"])

# ================= Flex 字典產生器 =================
def store_flex_dict(store):
    """回傳 Bubble 字典結構，確保 desc 絕不為空"""
    name = store.get("name", "未知店家")
    desc = clean_html(store.get("description"))
    if not desc: desc = "這是一間位於埔里的在地美食推薦 ✨"

    return {
        "type": "bubble",
        "size": "micro",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": name, "weight": "bold", "size": "lg", "wrap": True},
                {"type": "text", "text": desc, "wrap": True, "size": "xs", "color": "#8c8c8c", "maxLines": 3}
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

def category_quick_reply():
    """產生分類快速選單"""
    items = [
        QuickReplyItem(action=MessageAction(label=cat[:20], text=cat))
        for cat in FOOD_DATABASE.keys()
    ]
    return QuickReply(items=items[:13])

# ================= Webhook 與發送 =================
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
    return "Puli Food Bot is active!"

def send_reply(event, messages):
    with ApiClient(configuration) as api:
        line_bot_api = MessagingApi(api)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=messages
            )
        )

# ================= 訊息處理邏輯 =================
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    raw_msg = event.message.text.strip()
    
    # 執行斷詞
    tokens = jieba.lcut(raw_msg.lower()) if is_chinese(raw_msg) else raw_msg.lower().split()

    found_category = None
    found_store = None

    # 1. 招呼語 (修正冒號錯誤)
    keywords = ["你好", "嗨", "hello", "hi", "餓", "美食"]
    if any(k in raw_msg.lower() for k in keywords):
        send_reply(event, [TextMessage(
            text="你好！我是埔里美食小助手 🍜\n想吃哪一類？",
            quick_reply=category_quick_reply()
        )])
        return

    # 2. 隨機推薦
    if any(w in raw_msg for w in INTENT_RANDOM):
        all_stores = [s for stores in FOOD_DATABASE.values() for s in stores]
        if all_stores:
            found_store = random.choice(all_stores)

    # 3. 再推薦一次
    if "再" in raw_msg and user_id in user_last_category:
        found_category = user_last_category[user_id]

    # 4. 分類搜尋
    if not found_store and not found_category:
        for cat in FOOD_DATABASE:
            if any(w == cat or w in cat for w in tokens):
                found_category = cat
                user_last_category[user_id] = cat
                break

    # 5. 店名搜尋
    if not found_store and not found_category:
        for stores in FOOD_DATABASE.values():
            for s in stores:
                if any(w in s["name"] for w in tokens if len(w) > 1):
                    found_store = s
                    break
            if found_store: break

    # -------- 回覆內容組合 --------
    if found_category:
        stores = FOOD_DATABASE.get(found_category, [])
        if stores:
            selected = random.sample(stores, min(5, len(stores)))
            bubbles = [store_flex_dict(s) for s in selected]
            
            # 使用 from_dict 徹底解決 400 錯誤與 ImportError
            flex_msg = FlexMessage.from_dict({
                "altText": f"{found_category} 推薦清單",
                "contents": {
                    "type": "carousel",
                    "contents": bubbles
                }
            })
            send_reply(event, [flex_msg])
            return

    if found_store:
        bubble = store_flex_dict(found_store)
        flex_msg = FlexMessage.from_dict({
            "altText": found_store["name"],
            "contents": bubble
        })
        send_reply(event, [flex_msg])
        return

    # 沒找到結果
    send_reply(event, [TextMessage(
        text=f"找不到「{raw_msg}」的相關美食 😅\n可以試試看這些分類：",
        quick_reply=category_quick_reply()
    )])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
