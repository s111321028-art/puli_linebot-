import os
import zipfile
import random
import jieba 
from lxml import etree
from flask import Flask, request, abort
# LINE SDK v3 匯入
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent 

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

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

# Webhook 路由 
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

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text.strip().lower()
    
    # 預設變數
    found_category = None
    found_store = None
    reply_text = ""

    # 1. 處理招呼語
    if any(kw in user_msg for kw in ["hello", "你好", "嗨", "hi"]):
        categories = "、".join(FOOD_DATABASE.keys())
        reply_text = f"你好！我是埔里美食小助手 🤗\n目前的分類有：\n\n{categories}\n\n你想吃哪一類？"

    # 2. 判斷是否為「分類」關鍵字（包含模糊比對）
    if not reply_text:
        for category in FOOD_DATABASE.keys():
            if user_msg in category.lower() or category.lower() in user_msg:
                found_category = category
                break

    # 3. 如果不是分類，則進行「全資料庫店名搜尋」
    if not reply_text and not found_category:
        for category_name, stores in FOOD_DATABASE.items():
            for store in stores:
                # 模糊搜尋：判斷使用者輸入是否在店名內
                if user_msg in store['name'].lower():
                    found_store = store
                    break
            if found_store: break

    # --- 根據搜尋結果組合回覆訊息 ---
    
    if found_category:
        # 使用者輸入的是分類 (例如：飯、素、餐廳)
        stores = FOOD_DATABASE[found_category]
        sample_size = min(len(stores), 5)
        random_stores = random.sample(stores, sample_size)
        
        reply_text = f"🔍 幫你從「{found_category}」挑選幾間：\n"
        for s in random_stores:
            reply_text += f"📍 {s['name']}\n"
        reply_text += "\n可以直接輸入「完整店名」查看詳細介紹喔！"

    elif found_store:
        # 使用者輸入的不是分類，但在資料庫中找到了店名
        # 這裡加入你要求的「你是再說這個嗎」邏輯
        name = found_store['name']
        desc = found_store['description']
        
        # 處理 KML 中可能存在的 HTML 標籤（簡單清除或是保留）
        # 如果你想讓 LINE 顯示更乾淨，可以用 .replace('<b>', '').replace('</b>', '')
        clean_desc = desc.replace('<br>', '\n').replace('<b>', '').replace('</b>', '')
        
        reply_text = f"🧐 你是在說這一間嗎？\n\n🏠【{name}】\n{clean_desc}"

    elif not reply_text:
        # 都沒找到
        categories = "、".join(FOOD_DATABASE.keys())
        reply_text = f"抱歉，我找不到關於「{user_msg}」的店家或分類 😅\n\n試試看以下分類：\n{categories}"

    # 送出訊息
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        ))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

