FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

RUN pip install --no-cache-dir fastapi "uvicorn[standard]" transformers accelerate

WORKDIR /app
COPY serve.py /app/serve.py

ENV HF_ENDPOINT=https://hf-mirror.com
EXPOSE 8000

CMD ["python", "/app/serve.py"]
