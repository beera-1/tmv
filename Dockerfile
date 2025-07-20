FROM python:3.12.7

WORKDIR /MadxBotz

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python3", "bot.py"]
