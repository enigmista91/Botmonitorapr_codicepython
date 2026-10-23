FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY odoo_pr_bot/ ./odoo_pr_bot/
COPY pyproject.toml README.md ./

EXPOSE 8000

CMD ["python", "-m", "odoo_pr_bot.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
