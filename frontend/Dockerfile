# syntax=docker/dockerfile:1

# --- dev stage (local hot reload via docker-compose) ---
FROM node:24-alpine AS dev

RUN corepack enable

ENV PNPM_HOME=/pnpm \
    CI=1
RUN echo "store-dir=/pnpm/store" > /root/.npmrc

WORKDIR /app

# The committed lockfile only — never re-resolve at build time.
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,target=/pnpm/store \
    pnpm install --frozen-lockfile

COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

EXPOSE 5173

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["pnpm", "dev", "--host", "0.0.0.0"]

# --- builder stage (production build) ---
FROM node:24-alpine AS builder

RUN corepack enable

ENV PNPM_HOME=/pnpm \
    CI=1

WORKDIR /app

COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,target=/pnpm/store \
    pnpm install --frozen-lockfile

COPY . .

ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL
ARG VITE_APP_NAME
ENV VITE_APP_NAME=$VITE_APP_NAME

RUN pnpm build

# --- production stage (nginx serving built static files) ---
FROM nginx:alpine AS production

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
