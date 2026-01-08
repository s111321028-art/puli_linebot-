import os
import zipfile
import random
import jieba
import math
from lxml import etree
from flask import Flask, request, abort

# LINE SDK v3 匯入
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, 
    TextMessage, QuickReply, QuickReplyItem, MessageAction, LocationAction
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, LocationMessageContent

app = Flask(__name__)

# --- 1. 配置區 ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. 核心算法：距離計算 (Haversine Formula) ---
def get_distance(lat1, lon1, lat2, lon2):
    R = 6371  # 地球半徑 (km)
    dlat = math.radians(float(lat2) - float(lat1))
    dlon = math.radians(float(lon2) - float(lon1))
    a = math.sin(dlat/2)**2 + math.cos(math.radians(float(lat1))) * \
        math.cos(math.radians(float(lat2))) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

# --- 3. 資料庫讀取邏輯 ---
def load_food_data(file_path):
    food_db = {}
    try:
        if not os.path.exists(file_path):
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
        
        for folder in folders:
            cat_name = folder.xpath("./*[local-name()='name']/text()")
            cat_name = cat_name[0] if cat_name else "其他"
            p_in_folder = folder.xpath(".//*[local-name()='Placemark']")
            stores = []
            for p in p_in_folder:
                name = p.xpath("./*[local-name()='name']/text()")
                desc = p.xpath("./*[local-name()='description']/text()")
                coords = p.xpath(".//*[local-name()='coordinates']/text()")
                if name and coords:
                    parts = coords[0].strip().split(',')
                    stores.append({
                        "name": str(name[0]),
                        "description": str(desc[0]) if desc else "埔里美食",
                        "lng": float(parts[0]),
                        "lat": float(parts[1])
                    })
            if stores:
                food_db[cat_name] = stores
        return food_db
    except Exception as e:
        print(f"❌ 讀取失敗: {e}")
        return {}

FOOD_DATABASE = load_food_data('埔里吃什麼.kml')

# --- 4. 介面與功能 ---
def send_main_menu(reply_token):
    """依照流程圖：提供位置定位與分類篩選"""
    quick_replies = QuickReply(items=[
        QuickReplyItem(action=LocationAction(label="📍 傳送我的位置")),
        QuickReplyItem(action=MessageAction(label="飯類", text="飯類")),
        QuickReplyItem(action=MessageAction(label="麵類", text="飯類")),
        QuickReplyItem(action=MessageAction(label="早午餐", text="早午餐")),
        QuickReplyItem(action=MessageAction(label="素食", text="素食")),
        QuickReplyItem(action=MessageAction(label="小吃", text="小吃")),
        QuickReplyItem(action=MessageAction(label="炸物/烤物", text="炸物/烤物")),
        QuickReplyItem(action=MessageAction(label="桌菜", text="桌菜")),
        QuickReplyItem(action=MessageAction(label="飲料/甜點/冰品", text="飲料/甜點/冰品")),
        QuickReplyItem(action=MessageAction(label="隨便", text="隨便")),
    ])
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="你好，我是美食機器人，請告訴我你的位置，或選擇你想吃的分類！", quick_reply=quick_replies)]
            )
        )

# --- 5. 事件處理 ---

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    user_msg = event.message.text.strip().lower()
    
    if any(kw in user_msg for kw in ["hello", "你好", "嗨", "開始", "餓"]):
        send_main_menu(event.reply_token)
        return

    # 搜尋分類與隨機推薦邏輯 (略，同前版本)
    # ... 

@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location(event):
    """流程圖核心：後端處理地理座標定位"""
    user_lat = event.message.latitude
    user_lng = event.message.longitude
    
    # 篩選 3km 內的店家 (對應流程圖中的單車/機車範圍)
    nearby_stores = []
    for cat, stores in FOOD_DATABASE.items():
        for s in stores:
            dist = get_distance(user_lat, user_lng, s['lat'], s['lng'])
            if dist <= 3.0: # 3公里內
                s['distance'] = dist
                nearby_stores.append(s)
    
    # 排序並取前 5 名
    nearby_stores.sort(key=lambda x: x['distance'])
    top_stores = nearby_stores[:5]
    
    if not top_stores:
        reply_text = "附近 3 公里內找不到 KML 資料庫中的美食喔..."
    else:
        reply_text = f"📍 找到附近 3km 內的推薦店家：\n"
        for s in top_stores:
            reply_text += f"\n🍴 {s['name']} ({s['distance']:.1f}km)"
        reply_text += "\n\n點選店名可看詳細介紹！"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
