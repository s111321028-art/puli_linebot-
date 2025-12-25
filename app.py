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
from linebot.v3.messaging import QuickReply, QuickReplyItem, MessageAction

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

        # 讀取 KML 內容
        if zipfile.is_zipfile(file_path):
            with zipfile.ZipFile(file_path, 'r') as z:
                kml_content = z.read('doc.kml')
        else:
            with open(file_path, 'rb') as f:
                kml_content = f.read()

        parser = etree.XMLParser(recover=True)
        root = etree.fromstring(kml_content, parser=parser)
        
        # 尋找所有資料夾（分類）
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
                    coords = p.xpath(".//*[local-name()='coordinates']/text()")
                    
                    store_info = {
                        "name": str(name[0]) if name else "未知名稱",
                        "description": str(desc[0]) if desc else "埔里在地美食",
                        "lat": None,
                        "lng": None
                    }
                    
                    if coords:
                        # KML 格式: lng,lat,alt
                        parts = coords[0].strip().split(',')
                        if len(parts) >= 2:
                            store_info['lng'] = parts[0]
                            store_info['lat'] = parts[1]
                            
                    stores.append(store_info)
                
                if stores:
                    food_db[cat_name] = stores
        else:
            # 若無資料夾結構，抓取所有 Placemark
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

# 預先載入資料
FOOD_DATABASE = load_food_data('埔里吃什麼.kml')

def update_jieba_dict(food_db):
    for category in food_db.keys():
        jieba.add_word(category)
    for category_stores in food_db.values():
        for store in category_stores:
            jieba.add_word(store['name'])

if FOOD_DATABASE:
    update_jieba_dict(FOOD_DATABASE)

def send_welcome_menu(reply_token):
    categories = list(FOOD_DATABASE.keys()) 
    quick_replies = [QuickReplyItem(action=MessageAction(label=c, text=c)) for c in categories[:13]]
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(
                    text="想吃哪一類的埔里美食呢？或是直接輸入店名也可以喔！",
                    quick_reply=QuickReply(items=quick_replies)
                )]
            )
        )

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
    return "Puli Food Bot is running!"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text.strip().lower()
    words = list(jieba.cut(user_msg))
    
    reply_text = ""

    # 招呼語
    if any(kw in words for kw in ["hello", "你好", "嗨", "hi", "開始", "選單","餓", "吃", "喝", "隨便", "推薦"]):
        send_welcome_menu(event.reply_token)
        return

    # 搜尋邏輯 (分類或店家)
    if not reply_text:
        found_category = None
        found_store = None

        # 先搜尋分類
        for category in FOOD_DATABASE.keys():
            if user_msg in category.lower() or category.lower() in user_msg:
                found_category = category
                break
        
        # 若非分類，搜尋店家名
        if not found_category:
            for category_stores in FOOD_DATABASE.values():
                for store in category_stores:
                    if user_msg in store['name'].lower():
                        found_store = store
                        break
                if found_store: break

        if found_category:
            stores = FOOD_DATABASE[found_category]
            sample_size = min(len(stores), 6)
            random_stores = random.sample(stores, sample_size)
            reply_text = f"🔍 「{found_category}」推薦清單：\n"
            for s in random_stores:
                reply_text += f"📍 {s['name']}\n"
            reply_text += "\n可以直接輸入店名看詳細描述喔！"
        elif found_store:
            reply_text = f"🏠 店名：{found_store['name']}\n📝 描述：{found_store['description']}"
            if found_store.get('lat') and found_store.get('lng'):
                # 附帶 Google Maps 連結
                reply_text += f"\n🗺️ 地圖：https://www.google.com/maps?q={found_store['lat']},{found_store['lng']}"
        else:
            reply_text = f"抱歉，找不到關於「{user_msg}」的資訊。試試輸入「你好」看看分類清單！"

    # 回覆訊息
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

