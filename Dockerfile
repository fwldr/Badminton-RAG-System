# ============ API 运行镜像（FastAPI + Chroma + 百炼 OpenAI 兼容客户端） ============
# 前端由 web/ 独立 Dockerfile（nginx）提供；本镜像专注后端服务与入库。
FROM python:3.12-slim
WORKDIR /app

# chromadb 运行需要的基础库（curl 供健康检查使用；libgomp1 供 onnxruntime/RapidOCR）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY main.py .
# 入库流水线读取的清洗后 CSV（data/chroma 与 app.db 由卷提供，不入镜像）
COPY data/processed ./data/processed
# CLI 批量入库目录（pdf/图片；init 容器会扫描入库，运行产物不入卷）
COPY data/raw_docs ./data/raw_docs

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# 默认启动 API；init 容器通过 command 覆盖跑入库流水线
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
