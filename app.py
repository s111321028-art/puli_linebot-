import os
import zipfile
import random
import pandas as pd
from lxml import etree
from flask import Flask, request, abort

# Google AI 與 LINE SDK v3 匯入
from google import genai
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

# --- 1. 配置區 (從環境變數讀取) ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# --- 2. 資料庫讀取 (保留你的 KML 解析邏輯) ---
def load_food_data(file_path):
    food_list = [] # 為了給 LLM 更好讀，改用清單格式
    try:
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
        return food_list
    except Exception as e:
        print(f"❌ 讀取失敗: {e}")
        return []

# 啟動時讀取資料
FOOD_KNOWLEDGE = load_food_data('埔里吃什麼.kml')

# --- 3. 核心邏輯：檢索與生成 (RAG) ---
def get_ai_response(user_input):
    # 從資料庫篩選相關店家 (避免 Prompt 過長)
    # 資工系小撇步：這裡做簡單的關鍵字篩選，其餘交給 AI 判斷
    related_stores = [f"店名:{f['name']}, 介紹:{f['description']}" for f in FOOD_KNOWLEDGE if any(k in f['name'] or k in f['description'] for k in user_input)]
    
    # 限制給 AI 的知識量 (避免超過 Token 限制)
    context = "\n".join(related_stores[:10]) if related_stores else "請根據你對埔里的了解來回答。"

    system_prompt = f"""
    你是一位埔里美食專家。請參考以下【專屬美食資料庫】來回答使用者。
    如果資料庫有相關店家，請優先推薦；如果沒有，請用親切的語氣給予一般建議。
    回答請簡短有力，並加上適合的表情符號。
    
    【專屬美食資料庫】：
    {context}
    """

    try:
        response = gemini_client.models.generate_content(
            model="gemini-1.5-flash-002",
            config={'system_instruction': system_prompt},
            contents=user_input
        )
        return response.text
    except Exception as e:
        return f"我的大腦打結了... (錯誤原因: {str(e)})"

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
    return "Puli Food Bot is online!"
    
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text
    
    # 直接呼叫 AI 生成回覆
    reply_text = get_ai_response(user_msg)

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



