import os
import zipfile
import random
import jieba  # 修正：之前漏了匯入 jieba
from lxml import etree
from flask import Flask, request, abort

# LINE SDK v3 匯入
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent # 修正：v3 接收端應使用 TextMessageContent

app = Flask(__name__)

# --- 1. 配置區 ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. 資料庫讀取邏輯 ---
def load_food_data(file_path):
    food_db = {}
    try:
        if not os.path.exists(file_path):
            print(f"❌ 找不到檔案: {file_path}")
            return {}

        if zipfile.is_zipfile(file_path):
            with zipfile.ZipFile(file_path, 'r') as z:
                kml_content = z.read('doc.kml')
        else:
            with open(file_path, 'rb') as f:
                kml_content = f.read()

        parser = etree.XMLParser(recover=True)
        root = etree.fromstring(kml_content, parser=parser)
        
        folders = root.xpath(".//*[local-name()='Folder']")
        
        if folders:
            for folder in folders:
                cat_name = folder.xpath("./*[local-name()='name']/text()")
                cat_name = cat_name[0] if cat_name else "其他"
                
                p_in_folder = folder.xpath(".//*[local-name()='Placemark']")
                stores = []
                for p in p_in_folder:
                    name = p.xpath("./*[local-name()='name']/text()")
                    desc = p.xpath("./*[local-name()='description']/text()")
                    if name:
                        stores.append({
                            "name": str(name[0]),
                            "description": str(desc[0]) if desc else "埔里在地美食"
                        })
                if stores:
                    food_db[cat_name] = stores
        else:
            placemarks = root.xpath(".//*[local-name()='Placemark']")
            all_stores = []
            for p in placemarks:
                name = p.xpath("./*[local-name()='name']/text()")
                desc = p.xpath("./*[local-name()='description']/text()")
                if name:
                    all_stores.append({
                        "name": str(name[0]),
                        "description": str(desc[0]) if desc else "美食"
                    })
            if all_stores:
                food_db["全部美食"] = all_stores

        return food_db
    except Exception as e:
        print(f"❌ 讀取失敗: {e}")
        return {}

# 預先載入
FOOD_DATABASE = load_food_data('埔里吃什麼.kml')

def update_jieba_dict(food_db):
    for category in food_db.keys():
        jieba.add_word(category)
    for category_stores in food_db.values():
        for store in category_stores:
            jieba.add_word(store['name'])

if FOOD_DATABASE:
    update_jieba_dict(FOOD_DATABASE)

# --- 3. Webhook 路由 ---
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
    return "Puli Food Bot (Local DB Mode) is running!"

# 修正：v3 的 message 類型應為 TextMessageContent
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text.strip().lower()
    words = list(jieba.cut(user_msg))
    
    found_category = None
    found_store = None
    reply_text = ""

    # --- 邏輯判斷 ---
    if any(kw in words for kw in ["hello", "你好", "嗨", "hi"]):
        categories = "、".join(FOOD_DATABASE.keys())
        reply_text = f"你好！我是埔里美食小助手 🤗\n目前有這些分類：\n\n{categories}\n\n你想吃哪一類？"

    elif any(kw in user_msg for kw in ["餓", "吃", "喝", "隨便", "推薦"]):
        for category in FOOD_DATABASE.keys():
            if category in user_msg:
                found_category = category
                break
        if not found_category:
            categories = "、".join(FOOD_DATABASE.keys())
            reply_text = f"看到你說「{user_msg}」，肚子餓了嗎？😋\n試試輸入以下分類：\n\n{categories}"

    if not reply_text:
        # 搜尋分類
        for category in FOOD_DATABASE.keys():
            if user_msg in category.lower() or category.lower() in user_msg:
                found_category = category
                break
        
        # 搜尋店家
        if not found_category:
            for category_stores in FOOD_DATABASE.values():
                for store in category_stores:
                    if user_msg in store['name'].lower():
                        found_store = store
                        break
                if found_store: break

        if found_category:
            stores = FOOD_DATABASE[found_category]
            sample_size = min(len(stores), 5)
            random_stores = random.sample(stores, sample_size)
            reply_text = f"🔍 「{found_category}」推薦清單：\n"
            for s in random_stores:
                reply_text += f"📍 {s['name']}\n"
            reply_text += "\n可以直接輸入店名看詳細描述喔！"
        elif found_store:
            reply_text = f"🏠 店名：{found_store['name']}\n📝 描述：{found_store['description']}"
        else:
            reply_text = f"抱歉，找不到關於「{user_msg}」的資訊。試試輸入「你好」看看分類清單！"

    # 修正：LINE SDK v3 回覆訊息的正確語法
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
