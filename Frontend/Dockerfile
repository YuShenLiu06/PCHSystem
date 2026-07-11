# syntax=docker/dockerfile:1
# Frontend 容器镜像 —— 多阶段：node 构建 → nginx 托管
#
# 用法：由 docker-compose.yml 的 web 服务构建（build context = ./Frontend）。
# 产物：nginx:stable-alpine 内含 /usr/share/nginx/html（dist）+ /etc/nginx/conf.d/default.conf。
# /api/ 反代到 compose 服务名 backend:8000（同网络）；容器内固定监听 80，宿主端口由 WEB_PORT 决定。

ARG NPM_REGISTRY=""

# ---------- stage 1: 构建（容器内 npm build，零宿主 Node 依赖） ----------
FROM node:22-alpine AS builder
ARG NPM_REGISTRY
WORKDIR /app

# 先拷依赖清单，利用层缓存（源码改动不重装依赖）
COPY package.json package-lock.json* ./
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi \
 && (npm ci || npm install)

# 拷源码并构建（vue-tsc -b && vite build → /app/dist）
COPY . .
RUN npm run build

# ---------- stage 2: 托管 ----------
FROM nginx:stable-alpine
# dist 烘焙进镜像（非 bind-mount，故前端改动需重建镜像，由 update.sh 决策）
COPY --from=builder /app/dist /usr/share/nginx/html
# nginx 站点配置（try_files 兜底 history 路由 + /api/ 反代去前缀 + /assets 长缓存）
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
