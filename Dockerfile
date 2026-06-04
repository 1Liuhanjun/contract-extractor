FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p \
    data/uploads \
    data/results \
    data/reviews \
    data/reports \
    data/ocr_texts \
    Addition/output/contracts \
    Addition/output/reviews

EXPOSE 8080

CMD ["python", "src/webapp.py"]
