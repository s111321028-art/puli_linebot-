FROM python:3.11-slim

# 安裝 Chrome 和相關工具
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

# 這裡的 PORT 依然要跟 Render 配合
CMD ["python", "app.py"]
