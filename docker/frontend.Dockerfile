# syntax=docker/dockerfile:1
# Frontend image (React + Vite, served by nginx). Build context is the
# repository root, e.g.:
#   docker build -f docker/frontend.Dockerfile -t smart-locker-frontend:local .

# ---- Stage 1: build the static site ----
FROM node:22-alpine AS build
WORKDIR /app

# Install dependencies first so this layer is cached until package files change.
# `npm ci` needs frontend/package-lock.json (commit it). If your team switches
# to yarn or pnpm, change these two lines accordingly.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# Where the browser sends API calls. "/api" is proxied by nginx to the backend,
# so the browser only ever talks to one origin (no CORS setup needed).
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# ---- Stage 2: serve with nginx ----
FROM nginx:1.27-alpine

# The official nginx image renders every *.template file in this folder at
# container start, substituting ${ENV_VARS}. That is how BACKEND_URL gets in.
COPY docker/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

# Fallbacks only. docker-compose.yml and the Kubernetes manifests override them.
ENV BACKEND_URL=http://backend:8000 \
    GRAFANA_URL=http://grafana:3000 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1

EXPOSE 80
