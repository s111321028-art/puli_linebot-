import zipfile
import jieba
import random
from lxml import etree
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# LINE Bot

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

def load_food_data(file_path):
    food_db = {}
    try:
        # 1. 判斷是 KMZ (ZIP) 還是 KML (純文字)
        if zipfile.is_zipfile(file_path):
            with zipfile.ZipFile(file_path, 'r') as z:
                kml_content = z.read('doc.kml')
            print(f"📦 偵測到 KMZ 格式")
        else:
            with open(file_path, 'rb') as f:
                kml_content = f.read()
            print(f"📄 偵測到 KML (純文字) 格式")

        # 2. 解析 XML 內容
        parser = etree.XMLParser(recover=True)
        root = etree.fromstring(kml_content, parser=parser)
        
        # 3. 使用 local-name() 抓取所有地點，不管有沒有分層
        placemarks = root.xpath(".//*[local-name()='Placemark']")
        
        # 4. 抓取圖層名稱 (Folder) 作為分類，若無則歸類到 "一般"
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
            # 如果連一個 Folder 都沒有，全部塞進 "全部美食"
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

        print(f"✅ 解析成功！共讀取 {len(food_db)} 個分類")
        return food_db

    except Exception as e:
        print(f"❌ 讀取失敗: {e}")
        return {}

# 預先載入資料，避免每次訊息進來都重新解壓檔案（提升效率）
FOOD_DATABASE = load_food_data('埔里吃什麼.kml')
print("--- 資料庫讀取測試 ---")
if not FOOD_DATABASE:
    print("❌ 失敗：資料庫是空的，請檢查 .kmz 檔案路徑或內容")
else:
    print(f"✅ 成功：已讀取 {len(FOOD_DATABASE)} 個分類")
    for category, stores in FOOD_DATABASE.items():
        print(f" - 分類 [{category}]: 共有 {len(stores)} 間店家")
print("--------------------")

def update_jieba_dict(food_db):
    for category in food_db.keys():
        jieba.add_word(category)
    for category_stores in food_db.values():
        for store in category_stores:
            jieba.add_word(store['name'])

# 在啟動時呼叫一次
update_jieba_dict(FOOD_DATABASE)

# --- 3. Flask Route ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    # 1. 初始化與標準化輸入
    user_msg = event.message.text.strip().lower()
    words = list(jieba.cut(user_msg))
    print(f"NLP 分詞結果: {words}") 

    found_category = None
    found_store = None
    reply_text = ""

    # --- 優先順序 0：打招呼 ---
    if any(kw in words for kw in ["hello", "你好", "嗨", "hi"]):
        categories = "、".join(FOOD_DATABASE.keys())
        reply_text = f"你好！我是埔里美食小助手 🤗\n目前有這些分類：\n\n{categories}\n\n你想吃哪一類？"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # --- 優先順序 1：意圖判斷 (好餓、推薦、吃什麼) ---
    # 解決你提到的：如果輸入「好餓」，優先判斷為「餓」的意圖
    if any(kw in user_msg for kw in ["餓", "吃", "喝", "隨便", "推薦"]):
        for category in FOOD_DATABASE.keys():
            if category in user_msg:
                found_category = category
                break
        
        # 如果句子裡沒有提到特定分類，才列出清單
        if not found_category:
            categories = "、".join(FOOD_DATABASE.keys())
            reply_text = f"看到你說「{user_msg}」，看來是肚子餓了！😋\n埔里有這些分類，你想看哪一類？\n\n{categories}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

    # --- 優先順序 2：具體搜尋 (如果上面沒攔截到，代表使用者在找特定店家或分類) ---
    if not found_category:
        # 修正：使用 user_msg (字串) 去比對 category (字串)
        for category in FOOD_DATABASE.keys():
            if user_msg in category.lower() or category.lower() in user_msg:
                found_category = category
                break
            
    if not found_category:
        for category_stores in FOOD_DATABASE.values():
            for store in category_stores:
                if user_msg in store['name'].lower():
                    found_store = store
                    break
            if found_store: break

    # --- 3. 根據最終比對結果組合回覆 ---
    if found_category:
        stores = FOOD_DATABASE[found_category]
        reply_text = f"🔍 幫你找到「{found_category}」相關推薦：\n\n"
        sample_size = min(len(stores), 8)
        random_stores = random.sample(stores, sample_size)
        for store in random_stores:
            reply_text += f"📍 {store['name']}\n"
        reply_text += "\n你想看哪一間的詳細描述呢？"

    elif found_store:
        reply_text = f"🏠 店名：{found_store['name']}\n📝 描述：{found_store['description']}"

    else:
        reply_text = f"你說了「{user_msg}」，我不明白。請在輸入一次"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

if __name__ == "__main__":
    # 從環境變數讀取 PORT，若無則預設 5000
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)



