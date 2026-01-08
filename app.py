import os
import zipfile
import random
import jieba
import math
import time
from lxml import etree
from flask import Flask, request, abort
from datetime import datetime, timedelta

# LINE SDK v3 匯入
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, 
    TextMessage, FlexMessage, FlexContainer, QuickReply, QuickReplyItem, 
    MessageAction, LocationAction, PostbackAction
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, LocationMessageContent, PostbackEvent

# 爬蟲與瀏覽器自動化
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

app = Flask(__name__)

# --- 1. 配置區 ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. 核心算法：距離計算 (Haversine Formula) ---
def get_distance(lat1, lon1, lat2, lon2):
    """
    計算球面兩點間的距離
    r"$$d = 2R \cdot \arcsin\left(\sqrt{\sin^2\left(\frac{\Delta\phi}{2}\right) + \cos\phi_1\cos\phi_2\sin^2\left(\frac{\Delta\lambda}{2}\right)}\right)$$"
    """
    R = 6371  # 地球半徑 (km)
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

# --- 3. 資料庫讀取與處理 ---
def load_food_data(file_path):
    food_db = {}
    if not os.path.exists(file_path): return {}
    try:
        if zipfile.is_zipfile(file_path):
            with zipfile.ZipFile(file_path, 'r') as z:
                kml_content = z.read('doc.kml')
        else:
            with open(file_path, 'rb') as f:
                kml_content = f.read()

        root = etree.fromstring(kml_content, parser=etree.XMLParser(recover=True))
        folders = root.xpath(".//*[local-name()='Folder']")
        for folder in folders:
            cat_name = folder.xpath("./*[local-name()='name']/text()")[0]
            p_list = folder.xpath(".//*[local-name()='Placemark']")
            stores = []
            for p in p_list:
                name = p.xpath("./*[local-name()='name']/text()")
                desc = p.xpath("./*[local-name()='description']/text()")
                coords = p.xpath(".//*[local-name()='coordinates']/text()")
                if name and coords:
                    lng, lat, _ = coords[0].strip().split(',')
                    stores.append({
                        "name": str(name[0]),
                        "description": str(desc[0]) if desc else "埔里在地美食",
                        "lng": float(lng),
                        "lat": float(lat)
                    })
            food_db[cat_name] = stores
        return food_db
    except Exception as e:
        print(f"Error loading KML: {e}")
        return {}

FOOD_DATABASE = load_food_data('埔里吃什麼.kml')

# --- 4. 免費爬蟲模組 (Selenium) ---
def get_google_reviews(store_name):
    """對應流程圖：Google店家評論爬蟲"""
    options = Options()
    options.add_argument("--headless")  # 無介面模式
    options.add_argument("--no-sandbox")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    try:
        driver.get(f"https://www.google.com/maps/search/?api=1&query={store_name}+埔里")
        time.sleep(3)
        # 簡單抓取星等與第一則評論
        rating = driver.find_element(By.CLASS_NAME, "TTNxZf").text[:3] # 假設類名
        review_text = driver.find_element(By.CLASS_NAME, "wiI770").text # 假設類名
        return {"rating": rating, "review": review_text}
    except:
        return None
    finally:
        driver.quit()

# --- 5. UI 元件：Flex Message ---
def create_store_bubble(store):
    """創建美觀的店家卡片"""
    return {
      "type": "bubble",
      "body": {
        "type": "box", "layout": "vertical",
        "contents": [
          {"type": "text", "text": store['name'], "weight": "bold", "size": "xl"},
          {"type": "text", "text": f"📍 距離您 {store['distance']:.2f} km", "size": "sm", "color": "#666666"},
          {"type": "text", "text": store['description'][:60] + "...", "margin": "md", "wrap": True, "size": "sm"}
        ]
      },
      "footer": {
        "type": "box", "layout": "vertical", "spacing": "sm",
        "contents": [
          {"type": "button", "style": "primary", "action": {"type": "postback", "label": "查看 AI 評論分析", "data": f"action=analyze&name={store['name']}"}},
          {"type": "button", "style": "link", "action": {"type": "uri", "label": "Google 地圖導航", "uri": f"https://www.google.com/maps/search/?api=1&query={store['lat']},{store['lng']}"}}
        ]
      }
    }

# --- 6. 事件處理邏輯 ---

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
    user_msg = event.message.text.strip()
    # 呼叫選單
    if any(kw in user_msg for kw in ["開始", "你好", "餓", "吃"]):
        quick_replies = QuickReply(items=[
            QuickReplyItem(action=LocationAction(label="📍 傳送位置推薦")),
            QuickReplyItem(action=MessageAction(label="隨便推薦", text="隨便")),
            QuickReplyItem(action=MessageAction(label="看所有分類", text="分類"))
        ])
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="你好！我是埔里美食小助手 🤖\n請分享您的位置，或選擇下方功能：", quick_reply=quick_replies)]
            ))

@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location(event):
    """對應流程圖：地理座標定位 -> 篩選推薦範圍"""
    u_lat, u_lng = event.message.latitude, event.message.longitude
    nearby = []
    for cat, stores in FOOD_DATABASE.items():
        for s in stores:
            dist = get_distance(u_lat, u_lng, s['lat'], s['lng'])
            if dist <= 3.0: # 篩選 3km 內
                s['distance'] = dist
                nearby.append(s)
    
    nearby.sort(key=lambda x: x['distance'])
    bubbles = [create_store_bubble(s) for s in nearby[:10]] # LINE 限制 Carousel 最多 10 筆
    
    flex_msg = FlexMessage(alt_text="為您找到附近的推薦美食", contents={"type": "carousel", "contents": bubbles})
    
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[flex_msg]
        ))

@handler.add(PostbackEvent)
def handle_postback(event):
    """對應流程圖：執行爬蟲與分析"""
    data = event.postback.data
    if "action=analyze" in data:
        name = data.split("name=")[1]
        # 這裡執行爬蟲 (注意：在生產環境建議使用非同步)
        result = get_google_reviews(name)
        if result:
            reply = f"📊 「{name}」Google 評價：{result['rating']} ⭐\n\n📝 近期評論節錄：\n{result['review'][:100]}..."
        else:
            reply = f"暫時無法抓取「{name}」的詳細評論，請參考 KML 描述。"
        
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)]
            ))

if __name__ == "__main__":
    # Render 會透過環境變數指定 PORT，若本地執行則預設為 5000
    port = int(os.environ.get("PORT", 5000))
    # host 必須設定為 0.0.0.0 才能讓外部存取
    app.run(host='0.0.0.0', port=port)



