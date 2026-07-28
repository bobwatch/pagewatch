FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir -e .

EXPOSE 8787

VOLUME ["/data"]
ENV PAGEWATCH_HOME=/data

CMD ["pagewatch", "serve", "--host", "0.0.0.0"]