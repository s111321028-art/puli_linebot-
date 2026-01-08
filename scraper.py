import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

def get_google_reviews(store_name):
    """
    對應流程圖：資料庫建置 -> Google 店家評論爬蟲
    """
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    try:
        # 搜尋店家
        search_url = f"https://www.google.com/maps/search/{store_name}+埔里"
        driver.get(search_url)
        time.sleep(3)
        
        # 模擬點擊「評論」標籤 (標籤定位可能隨 Google 更新變動)
        # 這裡僅示範核心邏輯：抓取前 5 則近三個月內的評論
        review_elements = driver.find_elements(By.CLASS_NAME, "jfti5e") 
        
        reviews_list = []
        for r in review_elements:
            try:
                rating = int(r.find_element(By.CLASS_NAME, "kvMY9b").get_attribute("aria-label").split()[0])
                text = r.find_element(By.CLASS_NAME, "wiI770").text
                # 簡易情緒分類邏輯 (流程圖：回應該情緒分類)
                sentiment = "正面" if rating >= 4 else "負面"
                reviews_list.append({"rating": rating, "text": text, "sentiment": sentiment})
            except:
                continue

        if not reviews_list:
            return None

        # 篩選近三個月最好與最壞
        best = max(reviews_list, key=lambda x: x['rating'])
        worst = min(reviews_list, key=lambda x: x['rating'])
        
        return {"best": best, "worst": worst}
    except Exception as e:
        print(f"Scraper Error: {e}")
        return None
    finally:
        driver.quit()
