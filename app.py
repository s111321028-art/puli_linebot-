import os
import zipfile
import random
from lxml import etree
from flask import Flask, request, abort

# 僅保留 LINE SDK 匯入，刪除 Google AI 相關
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

# --- 1. 配置區 ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. 資料庫讀取 (維持原樣) ---
def load_food_data(file_path):
    food_list = []
    try:
        if not os.path.exists(file_path):
            print(f"❌ 找不到檔案: {file_path}")
            return []
            
        if zipfile.is_zipfile(file_path):
            with zipfile.ZipFile(file_path, 'r') as z:
                kml_content = z.read('doc.kml')
        else:
            with open(file_path, 'rb') as f:
                kml_content = f.read()

        parser = etree.XMLParser(recover=True)
        root = etree.fromstring(kml_content, parser=parser)
        placemarks = root.xpath(".//*[local-name()='Placemark']")

        for p in placemarks:
            name = p.xpath("./*[local-name()='name']/text()")
            desc = p.xpath("./*[local-name()='description']/text()")
            if name:
                food_list.append({
                    "name": str(name[0]),
                    "description": str(desc[0]) if desc else "埔里在地美食"
                })
        print(f"✅ 成功載入 {len(food_list)} 筆美食資料")
        return food_list
    except Exception as e:
        print(f"❌ 讀取失敗: {e}")
        return []

FOOD_KNOWLEDGE = load_food_data('埔里吃什麼.kml')

# --- 3. 核心邏輯：資料庫檢索 (代替 AI) ---
def get_db_response(user_input):
    # 1. 搜尋邏輯：檢查使用者輸入是否包含在店名或介紹中
    results = [f"🍴 {f['name']}\n📝 {f['description']}" for f in FOOD_KNOWLEDGE if user_input in f['name'] or user_input in f['description']]
    
    if results:
        # 如果找到太多筆，只取前 3 筆避免訊息過長
        count = len(results)
        reply = f"🔍 為您找到 {count} 筆相關美食：\n\n" + "\n\n---\n\n".join(results[:3])
        if count > 3:
            reply += "\n\n...(還有更多結果，請縮小關鍵字範圍)"
        return reply
    else:
        # 2. 沒找到時的 fallback：隨機推薦一筆
        random_store = random.choice(FOOD_KNOWLEDGE)
        return (f"找不到關鍵字「{user_input}」，不然試試這家：\n\n"
                f"🎲 隨機推薦：{random_store['name']}\n"
                f"📝 介紹：{random_store['description']}")

# --- 4. Webhook 路由 ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/", methods=['GET'])
def index():
    return f"Puli Food Bot (DB Mode) is online! Total: {len(FOOD_KNOWLEDGE)} stores."

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text
    
    # 直接從資料庫獲取回覆，不再呼叫 Gemini
    reply_text = get_db_response(user_msg)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
