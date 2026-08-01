FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY . .

RUN uv sync

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--reload"]