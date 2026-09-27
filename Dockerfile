FROM mcr.microsoft.com/playwright/python:v1.62.0-jammy

WORKDIR /app

# deps do projeto (inclui Flask)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# o playwright já vem com chromium no base image; garante permissões
RUN playwright install --with-deps chromium || playwright install chromium

COPY app.py olx_scraper.py openapi.yaml pyproject.toml ./
COPY src ./src
COPY templates ./templates
COPY static ./static 2>/dev/null || mkdir -p static

EXPOSE 8765

# persiste config/cookies/historico em /root/.olx (montado do host)
ENV OLX_GUI_HOST=0.0.0.0
ENV OLX_GUI_PORT=8765

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8765"]
