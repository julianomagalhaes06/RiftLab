# syntax=docker/dockerfile:1
FROM node:22.23.2-bookworm-slim AS runtime

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/index.html frontend/vite.config.ts frontend/tsconfig*.json ./
COPY frontend/src ./src
COPY frontend/public ./public

RUN npm run build

ENV NODE_ENV=production
EXPOSE 5173
# Dentro do container o bind é em todas as interfaces; o compose publica só em 127.0.0.1.
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0"]
