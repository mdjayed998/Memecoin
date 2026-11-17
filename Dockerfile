FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Long polling needs no open port / domain.
CMD ["python3", "bot.py"]
